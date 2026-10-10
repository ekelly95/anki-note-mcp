"""A real HTTP server that speaks — or deliberately mis-speaks — AnkiConnect.

There are no mocks in this project, matching obsidian-mcp and document-index-mcp. What
`mkdtemp` is to a vault and `pdfFixture.ts` is to a parser, this is to the
client: a genuine socket on an ephemeral port, so the code under test takes the
real httpx path through connect, read, status and decode.

That matters more here than it would elsewhere. The behaviour this client
exists to get right is failure — connection refused, a read that never returns,
a body that is not an envelope. Mocking the transport would replace exactly the
layer whose behaviour is in question.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class FakeAnki:
    """Scriptable AnkiConnect stand-in. Configure, then point a client at `url`."""

    def __init__(self) -> None:
        self.url: str = ""
        self.results: dict[str, Any] = {}
        self.errors: dict[str, str] = {}
        self.requests: list[dict[str, Any]] = []

        # The add-on's `apiKey` setting. None is its default, and matches a
        # request that sends no key at all — so a test that cares about
        # neither side gets the same behaviour it always had.
        self.api_key: str | None = None

        # Overrides for the malformed cases. When `raw_body` is set it is sent
        # verbatim, bypassing envelope construction entirely.
        self.status_code: int = 200
        self.raw_body: str | None = None
        # Sent on every reply, after the fake's own. For a reply that is not
        # malformed JSON but malformed HTTP — a `Content-Encoding` the body
        # does not honour.
        self.extra_headers: dict[str, str] = {}
        self.hang_seconds: float = 0.0

        # Accept the request, record it, then close the socket without
        # answering — a quitting Anki, and the one failure whose outcome is
        # genuinely unknowable from the client side. The request is recorded
        # BEFORE the close on purpose: that is what lets a test assert the far
        # end really did receive the write whose reply went missing, which is
        # the difference between "the note might exist" and a hypothetical.
        self.close_without_replying: bool = False

    # -- scripting -------------------------------------------------------
    def on(self, action: str, result: Any) -> None:
        """Make `action` succeed with `result`."""
        self.results[action] = result

    def fails(self, action: str, message: str) -> None:
        """Make `action` return a non-null `error` field."""
        self.errors[action] = message

    # -- assertions ------------------------------------------------------
    def last_request(self) -> dict[str, Any]:
        if not self.requests:
            raise AssertionError("no request was received")
        return self.requests[-1]


class _Handler(BaseHTTPRequestHandler):
    fake: FakeAnki

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        try:
            request = json.loads(body)
        except ValueError:
            request = {"_unparseable": body.decode("utf-8", "replace")}
        self.fake.requests.append(request)

        if self.fake.close_without_replying:
            # No status line, no headers, no body — httpx sees the connection
            # go away mid-request and raises RemoteProtocolError, which is the
            # real path `_TRANSPORT` exists for rather than a simulated one.
            self.close_connection = True
            return

        if self.fake.hang_seconds:
            time.sleep(self.fake.hang_seconds)

        if self.fake.raw_body is not None:
            payload = self.fake.raw_body.encode("utf-8")
        else:
            payload = json.dumps(self._reply(request)).encode("utf-8")

        try:
            self.send_response(self.fake.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            for name, value in self.fake.extra_headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Expected in the hang case: the client timed out and went away.
            # Windows reports that as ConnectionAbortedError (WinError 10053)
            # rather than either of the other two, which printed a traceback
            # from this thread into the suite output now and then.
            pass

    def _reply(self, request: Any) -> Any:
        """One reply, built the way `AnkiConnect.handler` builds it.

        Faithful rather than convenient, in three places that a plainer fake
        would smooth over and that this server actually depends on:

        - The key is compared unconditionally, so sending one to an add-on
          that has none configured fails exactly like sending the wrong one.
        - A failure is always wrapped in an envelope, whatever the version.
        - A success is wrapped only when the version is above 4. `multi` runs
          each sub-action through this same path with the sub-action's OWN
          version and key, so an unversioned sub-action comes back bare on
          success and wrapped on failure — indistinguishable.
        """
        request = request if isinstance(request, dict) else {}

        if request.get("key") != self.fake.api_key:
            return {"result": None, "error": "valid api key must be provided"}

        raw = request.get("action")
        action = raw if isinstance(raw, str) else ""

        if action in self.fake.errors:
            return {"result": None, "error": self.fake.errors[action]}

        if action == "multi":
            params = request.get("params") or {}
            result: Any = [self._reply(sub) for sub in params.get("actions") or []]
        else:
            result = self.fake.results.get(action)

        version = request.get("version")
        if not isinstance(version, int) or version <= 4:
            return result
        return {"result": result, "error": None}

    def log_message(self, format: str, *args: Any) -> None:
        # stderr is the diagnostic channel for the server under test; keep the
        # test output free of per-request access logging.
        pass


class _Server(ThreadingHTTPServer):
    # Do not let a hung request thread hold up shutdown. This restates the
    # stdlib default deliberately: the hang tests sleep for seconds inside a
    # handler, so it is load-bearing rather than incidental.
    daemon_threads = True


@contextmanager
def fake_anki() -> Iterator[FakeAnki]:
    """Run a FakeAnki on an ephemeral port for the duration of the block."""
    fake = FakeAnki()
    handler = type("_BoundHandler", (_Handler,), {"fake": fake})
    server = _Server(("127.0.0.1", 0), handler)
    fake.url = f"http://127.0.0.1:{server.server_address[1]}"

    # shutdown() blocks until serve_forever notices, so the default 0.5s poll
    # interval would add half a second of teardown to every test using a fake.
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    )
    thread.start()
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def unused_port() -> int:
    """A port with nothing listening on it — for the connection-refused path."""
    with closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
