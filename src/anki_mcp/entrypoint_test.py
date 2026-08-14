"""Invariant 9: stdout carries the protocol and nothing else.

A stray `print()` in a stdio server is silent corruption — it does not raise,
it does not log, it desynchronises the JSON-RPC stream and the host reports
something unrelated. The source spec names this rule explicitly, which is
reason enough to have a test rather than a comment.

This spawns the real entry point as a subprocess, so it also proves
`[project.scripts] anki-mcp = "anki_mcp.server:main"` actually starts.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from typing import Any

import pytest

from .server import _install_shutdown_handlers, _shutdown
from .testing.fake_anki import unused_port


def initialize_request(protocol_version: str = "2025-06-18") -> dict[str, Any]:
    """One `initialize`, with the revision the client claims to speak."""
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": protocol_version,
            "capabilities": {},
            "clientInfo": {"name": "entrypoint-test", "version": "0.0.0"},
        },
    }


INITIALIZE = initialize_request()


def run_entry_point(
    request: dict[str, Any] | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Spawn the real entry point, feed it one request, and close its stdin.

    The URL points at a port with nothing on it: the server must come up whether
    or not Anki is running, which is the whole degraded-mode premise.

    `encoding` is explicit rather than inherited. `text=True` alone decodes with
    the locale codec, which on Windows is cp1252 and raises UnicodeDecodeError on
    any non-ASCII character in a reply the server wrote correctly. Nothing sent
    here is non-ASCII today, which is exactly why that failure would arrive later
    and look like a defect in the server rather than in the harness.
    """
    env = {
        **os.environ,
        "ANKI_CONNECT_URL": f"http://127.0.0.1:{unused_port()}",
        **(extra_env or {}),
    }
    return subprocess.run(
        [sys.executable, "-m", "anki_mcp.server"],
        input=json.dumps(request if request is not None else INITIALIZE) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        env=env,
    )


def first_reply(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert lines, f"the server wrote nothing to stdout. stderr:\n{proc.stderr}"
    reply: dict[str, Any] = json.loads(lines[0])
    return reply


def test_the_entry_point_starts_and_writes_only_jsonrpc_to_stdout() -> None:
    proc = run_entry_point()

    stdout_lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert stdout_lines, f"the server wrote nothing to stdout. stderr:\n{proc.stderr}"

    for line in stdout_lines:
        try:
            message = json.loads(line)
        except ValueError:  # pragma: no cover - the failure this test exists for
            raise AssertionError(
                f"non-JSON on stdout, which desynchronises the protocol stream: {line!r}"
            ) from None
        assert message.get("jsonrpc") == "2.0", f"not a JSON-RPC message: {line!r}"

    first = json.loads(stdout_lines[0])
    assert first["id"] == 1
    server_info = first["result"]["serverInfo"]
    assert server_info["name"] == "anki-mcp"

    # FastMCP takes no `version` argument, so without an explicit assignment on
    # the wrapped low-level server this reports the MCP SDK's version instead.
    from . import __version__

    assert server_info["version"] == __version__, (
        f"the server reported version {server_info['version']!r}; if that is the "
        f"SDK's version, build_server stopped setting its own"
    )


def test_the_startup_banner_goes_to_stderr() -> None:
    """It is useful diagnostics, and it must never touch the protocol channel."""
    proc = run_entry_point()
    assert "anki-mcp ready" in proc.stderr
    assert "anki-mcp ready" not in proc.stdout


def test_closing_the_input_shuts_down_cleanly() -> None:
    """The path that actually runs main()'s `finally`, and the only one that
    runs on Windows: the host closes stdin rather than signalling. A traceback
    on the way out would mean the client is being closed during an unwind
    rather than after one."""
    proc = run_entry_point()
    assert proc.returncode == 0, f"exited {proc.returncode}. stderr:\n{proc.stderr}"
    assert "Traceback" not in proc.stderr, proc.stderr


def test_a_bad_environment_variable_is_a_message_rather_than_a_traceback() -> None:
    """config.py writes the message; main() was burying it.

    Every one of these messages names the variable, the values it accepts and
    what was actually found — and all of them were arriving underneath a stack
    trace, because create_context() sat outside main()'s only try. A host shows
    you a crashed subprocess, so a one-word typo read as a broken server. The
    Traceback assertion is the whole point of the test; the exit code is what
    distinguishes this from a clean shutdown.
    """
    proc = run_entry_point(extra_env={"ANKI_READ_ONLY": "maybe"})

    assert proc.returncode != 0, "a server that cannot read its configuration must not exit 0"
    assert "Traceback" not in proc.stderr, proc.stderr
    assert "ANKI_READ_ONLY" in proc.stderr, f"the message did not survive: {proc.stderr!r}"
    assert "'maybe'" in proc.stderr, "the message must say what it actually found"
    assert proc.stdout.strip() == "", "a startup failure must not write to the protocol channel"


# --- the SDK pin's canary ------------------------------------------------


def test_a_client_from_a_newer_protocol_revision_is_still_served() -> None:
    """The one test that would make the SDK migration urgent rather than optional.

    This server runs on the MCP SDK v1 line and speaks the newest revision that
    line knows, which is two behind the 2026-07-28 specification. A host built
    against that specification asks for a version this server cannot speak, and
    the handshake is supposed to settle on one both ends have — negotiated down,
    not refused. Verified by hand against a real client before this test existed,
    which is why it is written as an assertion rather than a hope.

    The day it fails, a host has stopped accepting the downgrade and moving to
    SDK 2.x has become the only option. `docs/roadmap.md` carries the decision
    this guards and the trigger for revisiting it.
    """
    proc = run_entry_point(initialize_request("2026-07-28"))
    reply = first_reply(proc)

    assert "error" not in reply, (
        f"a 2026-era client was refused rather than negotiated down: {reply['error']}. "
        f"The SDK ceiling in pyproject.toml is now load-bearing in the other direction."
    )
    assert reply["result"]["serverInfo"]["name"] == "anki-mcp"


def test_the_negotiated_version_is_the_newest_the_installed_sdk_knows() -> None:
    """Pinned against the SDK's own constant, never a literal.

    A hard-coded "2025-11-25" would break on the next SDK release and teach
    whoever hits it that the test is noise. What is worth pinning is the
    property — this server offers the newest revision available to it — which
    stays true across every version bump, and which would fail if `build_server`
    ever started naming a revision by hand.
    """
    from mcp.types import LATEST_PROTOCOL_VERSION

    reply = first_reply(run_entry_point(initialize_request("2026-07-28")))
    assert reply["result"]["protocolVersion"] == LATEST_PROTOCOL_VERSION


def test_a_termination_signal_unwinds_instead_of_killing_the_process() -> None:
    """Python's default for SIGTERM ends the process outright, so main()'s
    `finally` never runs. Called directly rather than raised for real: Windows
    delivers no SIGTERM at all, so a signal-based test would only ever prove
    something about the machine it ran on."""
    handler = signal.getsignal(signal.SIGTERM)
    try:
        _install_shutdown_handlers()
        assert signal.getsignal(signal.SIGTERM) is _shutdown
        assert signal.getsignal(signal.SIGINT) is _shutdown
        with pytest.raises(SystemExit):
            _shutdown(signal.SIGTERM, None)
    finally:
        signal.signal(signal.SIGTERM, handler)
