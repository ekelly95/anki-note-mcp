"""Environment-driven configuration.

Resolved through a factory that takes the environment as a parameter, rather
than through dataclass field defaults. Field defaults are evaluated once, at
class-definition time, so a `Config()` built after the environment changes
still carries whatever was set at import. The source spec's `config.py` has
exactly that shape, which makes the config untestable. This mirrors
`loadConfig(argv = process.argv.slice(2))` in obsidian-mcp and document-index-mcp:
the injectable parameter is the test seam.

Unlike those servers, every value here may default — but defaulting is not the
same as being harmless, and an earlier version of this docstring claimed the
worst a bad value could do was talk to nothing. That is not true of the URL.
The URL is not only where the writes go; it is where the API key and every
field value go, so a wrong one hands the collection to whatever answers rather
than to nothing. That is why it is parsed structurally below and not merely
prefix-checked. Malformed values fail loudly at startup rather than silently
reverting to a default.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from .fields import TRUNCATION_SUFFIX

DEFAULT_URL = "http://127.0.0.1:8765"

# Ceilings, not only floors. Failing loudly is supposed to work in both
# directions, and `ANKI_MAX_FIELD_CHARS=50000000` was accepted in silence.
# Each number is where the value stops meaning what its name says.
MAX_TIMEOUT_S = 300.0
"""Five minutes. Nothing on the other end is still waiting for the answer."""

MAX_SEARCH_RESULTS = 500
"""Every hit is a hydrated note, and this number is also published to clients
as the `limit` parameter's `le=` — a silly value ships a silly schema."""

MAX_SNIPPET_CHARS = 1000
"""Past this it is not a preview any more; reading a note is anki_get_note's job."""

MAX_FIELD_CHARS = 100_000
"""One field beyond this is not something to hand a model in one piece."""

MAX_RESPONSE_CHARS = 400_000
"""The per-field caps bound one field and said nothing about a whole response:
forty fields each just inside the cap returned 393,236 characters — about
98,000 tokens — from a single `anki_get_note`, with `truncated` empty because
every field was individually fine. This is the ceiling on the budget that
bounds the total."""


@dataclass(frozen=True)
class Config:
    """Resolved configuration. Build it with `load_config`, never by hand."""

    url: str
    """AnkiConnect endpoint. Binds 127.0.0.1:8765 by default in the add-on."""

    api_key: str | None
    """Optional; AnkiConnect's `apiKey` setting is unset by default."""

    timeout_s: float
    """One short timeout, no retries. A stall means a modal dialog or macOS App
    Nap, neither of which a retry can clear."""

    max_search_results: int
    """Hard ceiling on how many notes `anki_find_notes` will hydrate."""

    snippet_chars: int
    """Visible characters per search snippet, counted after HTML is stripped."""

    max_field_chars: int
    """Visible characters per field in `anki_get_note`."""

    max_response_chars: int
    """Characters of note content one tool call may return in total.

    Counts every copy actually sent — a field returned as both raw HTML and
    rendered text spends both — because the cost being bounded is the caller's
    context, not the collection's size.
    """

    read_only: bool = False
    """Refuse every tool that changes the collection: add, update, tag, delete
    and sync.

    Opt-in, so it defaults to False — but the *parsing* is strict, because a
    value this one silently misread would leave a collection writable while
    its owner believed otherwise. See `_flag`.
    """

    allow_sync: bool = False
    """Allow `anki_sync`. Off unless set, even when writes are allowed.

    Split out of `read_only` because sync is the one operation whose blast
    radius leaves this machine. An add or an update is a local change, visible
    in Anki and recoverable from a backup; a sync pushes whatever is in the
    collection to AnkiWeb and on to every other device, which is what turns a
    bad write into a distributed one. It is also the capability a session
    almost never needs — cards get written far more often than the collection
    gets pushed — so the cost of making it opt-in is close to nothing.

    Opposite polarity to `read_only`, which is deliberate rather than an
    oversight: what the two share is that the unset value is the safe one, and
    that is the property worth keeping consistent.
    """

    allow_delete: bool = False
    """Allow `anki_delete_notes`. Off unless set, even when writes are allowed.

    Split out of `read_only` for the reason `allow_sync` is, and more so. Every
    other write here can be put back: an add can be deleted, an update edited
    again, a tag removed. A deletion cannot — Anki has no trash, so the only way
    back is restoring a whole-collection backup, which also discards everything
    done since. It is also the capability an injected instruction in a shared
    deck would most want, so it is granted on its own and never by default.
    """


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Resolve configuration from the environment.

    Pass `env` in tests; production passes nothing and gets `os.environ`.
    """
    e: Mapping[str, str] = os.environ if env is None else env

    config = Config(
        url=_checked_url(e.get("ANKI_CONNECT_URL", DEFAULT_URL)),
        api_key=e.get("ANKI_CONNECT_API_KEY") or None,
        timeout_s=_bounded_float(e, "ANKI_CONNECT_TIMEOUT", 10.0, MAX_TIMEOUT_S),
        max_search_results=_bounded_int(e, "ANKI_MAX_SEARCH", 50, MAX_SEARCH_RESULTS),
        snippet_chars=_bounded_int(e, "ANKI_SNIPPET_CHARS", 120, MAX_SNIPPET_CHARS),
        max_field_chars=_bounded_int(e, "ANKI_MAX_FIELD_CHARS", 5000, MAX_FIELD_CHARS),
        max_response_chars=_bounded_int(e, "ANKI_MAX_RESPONSE_CHARS", 40_000, MAX_RESPONSE_CHARS),
        read_only=_flag(e, "ANKI_READ_ONLY"),
        allow_sync=_flag(e, "ANKI_ALLOW_SYNC"),
        allow_delete=_flag(e, "ANKI_ALLOW_DELETE"),
    )

    # Each bound is checked alone above; this is the one pair that can disagree.
    # A snippet that does not fit in the response budget makes every search
    # return nothing with a non-zero `total_matched` — a configuration that
    # looks like a broken server rather than a typo.
    widest = config.snippet_chars + len(TRUNCATION_SUFFIX)
    if widest > config.max_response_chars:
        raise ValueError(
            f"ANKI_MAX_RESPONSE_CHARS ({config.max_response_chars}) must leave room for "
            f"one search snippet, which ANKI_SNIPPET_CHARS ({config.snippet_chars}) allows "
            f"to be {widest} characters. Raise the first or lower the second."
        )
    return config


def _checked_url(url: str) -> str:
    """The endpoint, checked for the two things that are not merely typos.

    The scheme check is the obvious one. The userinfo check is not: a URL like
    `http://user:pw@host/` is perfectly valid, httpx sends it happily, and this
    value is printed verbatim in the startup banner — so embedded credentials
    would reach stderr from a server that is otherwise careful never to log the
    API key. Refusing the form here is one guard at the boundary, which is why
    no redaction helper is needed at the places the URL gets formatted.

    A missing host or an unusable port is also refused here, so that it fails at
    startup rather than on the first tool call.

    None of these checks restricts *which* host. Pointing this at a remote AnkiConnect
    stays possible on purpose: it is a supported deployment, the person setting
    the variable is the person whose collection it is, and a loopback allowlist
    would only be an escape hatch to write past.
    """
    if not url.startswith(("http://", "https://")):
        raise ValueError(
            f"ANKI_CONNECT_URL must start with http:// or https://, got {url!r}. "
            f"The AnkiConnect default is {DEFAULT_URL}."
        )

    try:
        parsed = urlsplit(url)
        # `urlsplit` defers the port, so read it here: `:abc` or `:99999`
        # otherwise reached httpx as `InvalidURL`, which is no `TransportError`
        # and so escaped the client untyped on the first tool call.
        parsed.port  # noqa: B018
        # `urlsplit` checks a bracketed host only from 3.10.12 and 3.11.4, and
        # 3.10.11 is the last 3.10 with a Windows installer. There `[oops]` got
        # through to httpx as the same untyped `InvalidURL`, so check it here.
        if parsed.netloc.rpartition("@")[2].startswith("["):
            ipaddress.IPv6Address(parsed.hostname or "")
    except ValueError as exc:
        # Not echoing the value: an unparseable URL can still contain a password.
        raise ValueError(f"ANKI_CONNECT_URL is not a valid URL: {exc}.") from exc

    if parsed.username or parsed.password:
        # Emphatically not echoing the value here — the password is in it, and
        # this message is headed for the same stderr the banner writes to.
        raise ValueError(
            "ANKI_CONNECT_URL must not carry a username or password. Anything sent "
            "to that URL includes your note content, and the URL itself is printed "
            "at startup. AnkiConnect authenticates with its own `apiKey` setting; "
            f"put that in ANKI_CONNECT_API_KEY instead. The default URL is {DEFAULT_URL}."
        )

    if not parsed.hostname:
        # After the userinfo check, so `http://user:pw@/` still gets the message
        # about credentials. Without this, every call failed as "Anki may have
        # been closed", which sends the reader to Anki rather than to the config.
        raise ValueError(f"ANKI_CONNECT_URL has no host. The AnkiConnect default is {DEFAULT_URL}.")

    return url


# Spelled out rather than `== "1"`, and strict rather than truthy. Both
# directions of the obvious shortcut fail in the dangerous direction:
# `raw == "1"` reads ANKI_READ_ONLY=true as writable, and `bool(raw)` reads
# ANKI_READ_ONLY=false as read-only. Anything not clearly one or the other is
# a typo in a host config, and this is the one setting where guessing wrong
# means a collection is writable while its owner believes it is not.
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off", ""})


def _flag(env: Mapping[str, str], name: str) -> bool:
    raw = env.get(name)
    if raw is None:
        return False
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(
        f"{name} must be one of 1/true/yes/on to enable, or 0/false/no/off to disable, got {raw!r}."
    )


def _bounded_int(env: Mapping[str, str], name: str, default: int, maximum: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a whole number, got {raw!r}.") from exc
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}, got {value}.")
    return value


def _bounded_float(env: Mapping[str, str], name: str, default: float, maximum: float) -> float:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number of seconds, got {raw!r}.") from exc
    if not 0 < value <= maximum:
        raise ValueError(f"{name} must be greater than 0 and at most {maximum:g}, got {value:g}.")
    return value
