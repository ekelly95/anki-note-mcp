"""The one place this server talks to AnkiConnect.

Every tool calls `invoke` (or its `_async` twin); no tool touches httpx or
JSON. That single choke point is what makes the safety properties checkable in
one file: the envelope is validated on every call — once per request, and once
per sub-action of a `multi` — and every failure leaves here as a typed
`AnkiError` carrying a message written for the model to act on.

Deliberately no retries. A stall is almost always a modal dialog holding Anki's
GUI thread, or macOS App Nap suspending the add-on; a retry clears neither and
only doubles the time before the caller is told what is wrong.

The `_async` twins exist because FastMCP runs a synchronous tool directly on
the event loop; see `invoke_async`. They are the only form the tools use.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping, Sequence
from functools import partial
from types import TracebackType
from typing import Any

import anyio.to_thread
import httpx

from .config import Config
from .errors import (
    AnkiAuthError,
    AnkiConnectError,
    AnkiError,
    AnkiNotRunningError,
    AnkiProtocolError,
)

# API version 6 is sent on every call and is not configurable. Omitting it
# defaults the add-on to version 4, and "when the provided version is level 4
# or below, the API response will only contain the value of the result; no
# error field is available" — which would silently disable every check below.
API_VERSION = 6

# A localhost TCP connect either completes in milliseconds or there is nothing
# listening, so it gets a much tighter budget than a read does. This is not
# theoretical: measured on this machine, connecting to a closed 127.0.0.1 port
# does not return ECONNREFUSED, it hangs until the timeout expires (Windows
# Firewall drops the SYN rather than rejecting it). Sharing one budget would
# make the commonest degraded case of all — Anki simply not running — take the
# full read timeout before the caller is told so.
CONNECT_TIMEOUT_S = 2.0

# How much of a batched call's parameters may appear in an error message.
_LABEL_CHARS = 60

# How much of a malformed *response* may appear in one. See `_summarize`: these
# bound its only unbounded input, an object's own key names.
_SUMMARY_KEYS = 10
_KEY_CHARS = 40


def _timeouts(timeout_s: float) -> httpx.Timeout:
    """Read/write/pool get the configured budget; connect gets a short one."""
    return httpx.Timeout(timeout_s, connect=min(CONNECT_TIMEOUT_S, timeout_s))


# Connection reuse is disabled deliberately. AnkiConnect closes pooled
# connections, and httpx races it: measured over 60 rapid sequential calls,
# keep-alive produced intermittent ReadErrors (60/60, 59/60, 55/60 across three
# runs) while a fresh connection each time gave 60/60 every run. That surfaced
# as a loop of anki_add_note calls dying half way with "Anki may have been
# closed" while Anki was perfectly healthy — and looping single adds is the
# designed usage of this server, so it is the one pattern that must not be
# flaky. Retrying is the wrong fix here: a ReadError cannot distinguish "never
# delivered" from "delivered, response lost", so retrying addNote risks writing
# the note twice. A TCP handshake to localhost costs microseconds.
_LIMITS = httpx.Limits(max_keepalive_connections=0)

_NOT_RUNNING = (
    "Cannot reach AnkiConnect at {url}. Anki desktop must be running with the "
    "AnkiConnect add-on (code 2055492159) installed. Open Anki, then retry."
)

_BLOCKED = (
    "AnkiConnect accepted the connection at {url} but did not respond within "
    "{timeout:g}s. A modal dialog in Anki (Add Cards, a sync or confirmation "
    "prompt) blocks its GUI thread and stalls every request until dismissed. "
    "Check the Anki window and dismiss any open dialog. {tail}"
)

_TRANSPORT = (
    "The connection to AnkiConnect at {url} failed mid-request ({detail}). "
    "Anki may have been closed or restarted. Check the Anki window. {tail}"
)

# Both messages above lose their connection AFTER it was established, so the
# request may or may not have been carried out — which matters for exactly one
# action. A read changes nothing, `updateNote` writes the same values a second
# time, and a second `sync` is the same request; `addNote` is the one that is
# not idempotent. The comment on _LIMITS above is already explicit that a lost
# response cannot be told apart from an undelivered request, and then these
# messages went on to say "retry" regardless. That is how one dropped reply
# becomes two notes.
_AMBIGUOUS = frozenset({"addNote"})

_RETRY = "Then retry."

_UNKNOWN_OUTCOME = (
    "Then check before retrying: the note may already have been added before the "
    "reply was lost, and nothing in this failure can tell that apart from the note "
    "never being written at all. Search for it with anki_find_notes, and add it "
    "again only if it is not there — a blind retry is how one lost reply becomes "
    "two copies of the same card."
)

# Read from the installed add-on (2055492159): a bad key is NOT an HTTP 403.
# `handler` raises Exception('valid api key must be provided'), catches it
# itself, and returns it as an ordinary error envelope with status 200 — so
# without this mapping a wrong key arrives as a generic AnkiConnectError and
# the actionable message below never fires.
#
# Anchored, not a substring search. `format_exception_reply` is
# `{"result": None, "error": str(exception)}`, so this message is always the
# WHOLE error string, never a fragment of a longer one — while other messages
# do interpolate caller data ("deck was not found: X"). An unanchored match
# would tell someone with a mistyped deck name to go and fix their API key,
# which is worse than the failure it is trying to improve on: unmatched, the
# add-on's own words still reach the caller intact. The live tier is what
# notices if the add-on ever rewords it.
_API_KEY_ERROR = re.compile(r"\A\s*valid api key must be provided\.?\s*\Z", re.IGNORECASE)

_BAD_KEY = (
    "AnkiConnect rejected the API key: {detail} Set ANKI_CONNECT_API_KEY to match the "
    "`apiKey` value in the add-on's configuration, or clear both. The add-on compares "
    "the two unconditionally, so sending a key when it has none configured fails "
    "exactly like sending the wrong one."
)

# The add-on's own 403 is unreachable from here: `allowOrigin` ends in a bare
# `else: allowed = True`, and httpx sends no Origin header. So a 403 means
# either something in front of AnkiConnect, or a configuration this client
# cannot produce. Kept as a backstop, but it has never been about the key.
_FORBIDDEN = (
    "AnkiConnect refused the request at {url} with HTTP 403. That is not a bad API "
    "key — a wrong key comes back as HTTP 200 with an error message. It means a "
    "proxy or tunnel in front of AnkiConnect rejected the request, or the add-on's "
    "`webCorsOriginList` did. Check whatever is listening on that URL."
)


def _refuse_to_block_the_event_loop() -> None:
    """Fail loudly if a blocking call is made from the event loop itself.

    A tool that calls `invoke` instead of `invoke_async` type-checks, passes
    review and works — it just quietly pins the server again, which is the
    defect the async twins exist to remove and the kind that only shows up
    under load. This turns that into an immediate, obvious failure, and makes
    every existing tool test a guard for it rather than only the one test that
    measures concurrency directly.

    `invoke_async` runs this in a worker thread, where there is no running
    loop, so the intended path is unaffected — as are the synchronous tests
    and the live tier. Deliberately not an `AnkiError`: nothing about the
    transport failed, this is a bug in the caller.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError(
        "AnkiClient.invoke blocks; calling it from the event loop stalls the whole "
        "server for the request's timeout. Await invoke_async (or invoke_multi_async) "
        "from an async tool instead."
    )


def _tail(action: str) -> str:
    """What to tell the caller to do next, given what this action would repeat."""
    return _UNKNOWN_OUTCOME if action in _AMBIGUOUS else _RETRY


def _error_for(message: str) -> AnkiError:
    """The typed error for one non-null `error` field."""
    if _API_KEY_ERROR.search(message):
        return AnkiAuthError(_BAD_KEY.format(detail=message.rstrip(".") + "."))
    return AnkiConnectError(message)


def _describe(action: str, params: Mapping[str, Any]) -> str:
    """Name one batched call for an error message.

    Thirty `modelFieldNames` calls can go out in a single `multi`; the action
    name on its own does not say which of them failed.

    Scalars only, and dropped entirely past `_LABEL_CHARS`. This helper is
    generic but its output ends up in a message the model reads, and the day
    someone batches an `addNote` a naive repr would paste a whole note's field
    HTML into an error — exactly the leak `anki_find_notes` is careful never
    to make.
    """
    scalars = {
        name: value for name, value in params.items() if isinstance(value, str | int | float | bool)
    }
    if len(scalars) != len(params):
        return action
    inner = ", ".join(f"{name}={value!r}" for name, value in scalars.items())
    if not inner or len(inner) > _LABEL_CHARS:
        return action
    return f"{action}({inner})"


def _summarize(value: Any) -> str:
    """Describe a malformed response without quoting any of it.

    The two callers below each hold a decoded response that turned out not to
    be the expected shape, and the obvious thing to write there is `{value!r}`
    — which is how a 21,000-character `notesInfo` reply ends up pasted whole
    into an error string and handed straight to the model, past every cap
    anki_find_notes applies and past the promise that search never returns full
    note content. `_describe` above exists for that exact reason on the request
    side; this is the response side of the same rule, and it was missing.

    Structure only: type, size, and for an object its keys. That is what
    diagnosing a bad envelope actually needs — the absent key IS the diagnosis
    — and saying nothing about values makes "no field content can reach a
    protocol error" a property of this function rather than a length that
    happens to be short enough today. Keys are response-controlled too, so they
    are capped in both count and width. The one reply where a preview genuinely
    helps is one that is not JSON at all, and `_unwrap` already prints a
    bounded 200 characters of that.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return f"the boolean {str(value).lower()}"
    if isinstance(value, int | float):
        return f"a bare {type(value).__name__}"
    if isinstance(value, str):
        return f"a string of {len(value)} characters"
    if isinstance(value, list):
        return f"a list of {len(value)} items"
    if isinstance(value, dict):
        if not value:
            return "an empty object"
        names = [str(name) for name in list(value)[:_SUMMARY_KEYS]]
        shown = ", ".join(
            repr(name if len(name) <= _KEY_CHARS else name[:_KEY_CHARS] + "…") for name in names
        )
        more = f", and {len(value) - _SUMMARY_KEYS} more" if len(value) > _SUMMARY_KEYS else ""
        return f"an object with keys [{shown}{more}]"
    return f"a {type(value).__name__}"


class AnkiClient:
    """A thin typed client. One contract: only `AnkiError` leaves it."""

    def __init__(self, config: Config, http: httpx.Client | None = None) -> None:
        self._cfg = config
        # Injectable so tests can point at the fake without monkeypatching.
        self._http = (
            http
            if http is not None
            else httpx.Client(timeout=_timeouts(config.timeout_s), limits=_LIMITS)
        )

    def _request(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """One AnkiConnect request object.

        Shared by the outer request and by every sub-action of a `multi`, which
        need their own copies of both fields — see `invoke_multi`.
        """
        request: dict[str, Any] = {
            "action": action,
            "version": API_VERSION,
            "params": params,
        }
        if self._cfg.api_key:
            request["key"] = self._cfg.api_key
        return request

    def invoke(self, action: str, **params: Any) -> Any:
        """Call one AnkiConnect action and return its `result`.

        Raises only `AnkiError` subclasses — never an httpx or json exception.
        Blocking, so tools must call `invoke_async` instead; see the guard.
        """
        _refuse_to_block_the_event_loop()
        payload = self._request(action, params)

        try:
            response = self._http.post(self._cfg.url, json=payload)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # Nothing is listening, or the connection could not be established.
            raise AnkiNotRunningError(_NOT_RUNNING.format(url=self._cfg.url)) from exc
        except httpx.TimeoutException as exc:
            # Connected, but no response: ReadTimeout / WriteTimeout / PoolTimeout.
            raise AnkiNotRunningError(
                _BLOCKED.format(
                    url=self._cfg.url,
                    timeout=self._cfg.timeout_s,
                    tail=_tail(action),
                )
            ) from exc
        except httpx.TransportError as exc:
            # RemoteProtocolError, ReadError and friends — e.g. Anki quit mid-request.
            raise AnkiNotRunningError(
                _TRANSPORT.format(
                    url=self._cfg.url,
                    detail=type(exc).__name__,
                    tail=_tail(action),
                )
            ) from exc

        return self._unwrap(action, response)

    async def invoke_async(self, action: str, **params: Any) -> Any:
        """`invoke`, off the event loop. This is what the tools call.

        Measured against mcp 1.29: FastMCP runs a synchronous tool directly on
        the asyncio loop (`func_metadata.call_fn_with_arg_validation` calls
        `fn(...)` when the tool is not a coroutine function), while the
        low-level server dispatches every request with `tg.start_soon`. So a
        blocking POST in a `def` tool pins the whole server for the full
        timeout: two concurrent calls against an AnkiConnect stalling 1.0s
        each took 2.03s, not 1.0s.

        What that buys is protocol liveness, not Anki throughput. AnkiConnect
        runs on Anki's GUI thread and serialises everything, so a second tool
        call still waits for the first at the far end — and if the stall is a
        modal dialog, `anki_status` waits with it. What no longer waits is the
        server itself: pings, `notifications/cancelled`, `tools/list` and
        `initialize` are answered while a call is in flight, so a 10s add no
        longer makes this server look dead to its host. Two timeouts also now
        run in parallel rather than end to end.

        Cancellation is left at anyio's default, which waits for the worker
        thread rather than abandoning it: an abandoned `addNote` would still
        write the note. The cost is that shutdown can take up to `timeout_s`
        if a call is in flight — no worse than the blocking version, which
        held the loop for exactly as long.
        """
        return await anyio.to_thread.run_sync(partial(self.invoke, action, **params))

    def invoke_multi(self, calls: Sequence[tuple[str, dict[str, Any]]]) -> list[Any]:
        """Run several actions in one HTTP round trip, results in order.

        Each sub-action carries its own `version` AND `key`, which is not
        belt-and-braces. Read from the installed add-on: `multi` is literally
        `list(map(self.handler, actions))`, and `handler` reads both fields
        from the request it is given — here, the sub-action, not the envelope
        around it. Omitting `version` defaults that sub-action to 4, which
        returns its result unwrapped on success but still wrapped on failure,
        so the two become indistinguishable. Omitting `key` fails every
        sub-action outright whenever an `apiKey` is configured.
        """
        if not calls:
            # A collection with no note types is not an error, and asking
            # AnkiConnect for nothing is still a round trip.
            return []

        actions = [self._request(action, params) for action, params in calls]
        results = self.invoke("multi", actions=actions)

        if not isinstance(results, list) or len(results) != len(actions):
            raise AnkiProtocolError(
                f"AnkiConnect returned {_summarize(results)} for a multi of "
                f"{len(actions)} actions. Expected a list of that many "
                f"{{result, error}} objects."
            )

        unwrapped: list[Any] = []
        for (action, params), item in zip(calls, results, strict=True):
            label = _describe(action, params)
            try:
                unwrapped.append(self._unwrap_envelope(label, item))
            except AnkiConnectError as exc:
                # The add-on's message says what went wrong but not which of
                # the batched calls it went wrong for. AnkiAuthError and
                # AnkiProtocolError are left alone: the first is about the
                # whole request, the second already carries the label.
                raise AnkiConnectError(f"{label}: {exc}") from exc
        return unwrapped

    async def invoke_multi_async(self, calls: Sequence[tuple[str, dict[str, Any]]]) -> list[Any]:
        """`invoke_multi`, off the event loop. See `invoke_async`."""
        return await anyio.to_thread.run_sync(partial(self.invoke_multi, calls))

    def _unwrap(self, action: str, response: httpx.Response) -> Any:
        """HTTP status and body, down to the envelope."""
        if response.status_code == 403:
            raise AnkiAuthError(_FORBIDDEN.format(url=self._cfg.url))
        if response.status_code >= 400:
            # Wrapped rather than raise_for_status(): an httpx.HTTPStatusError
            # escaping here would break the "only AnkiError leaves this module"
            # contract that every caller relies on.
            raise AnkiProtocolError(
                f"AnkiConnect returned HTTP {response.status_code} for action "
                f"{action!r}. Expected 200 with a {{result, error}} body."
            )

        try:
            data = response.json()
        except ValueError as exc:
            body = response.text[:200]
            raise AnkiProtocolError(
                f"AnkiConnect returned a non-JSON body for action {action!r}: {body!r}"
            ) from exc

        return self._unwrap_envelope(action, data)

    def _unwrap_envelope(self, action: str, data: Any) -> Any:
        """Validate one `{result, error}` object and return its result.

        Split out from `_unwrap` because `multi` produces one of these per
        sub-action, and every one of them has to be checked the same way or
        the choke-point guarantee only holds for unbatched calls. `action` is
        a label for the message, not necessarily a bare action name.
        """
        if not isinstance(data, dict) or "result" not in data or "error" not in data:
            raise AnkiProtocolError(
                f"Unexpected AnkiConnect response for action {action!r}: got "
                f"{_summarize(data)}. Expected an object with exactly `result` "
                f"and `error`."
            )

        if data["error"] is not None:
            raise _error_for(str(data["error"]))

        return data["result"]

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> AnkiClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
