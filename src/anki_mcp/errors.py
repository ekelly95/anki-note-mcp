"""The typed error hierarchy.

Every failure that crosses out of `AnkiClient.invoke` is one of these. Nothing
from httpx or json escapes, so a caller that handles `AnkiError` has handled
everything the transport can do to it. The source spec states this rule ("no
tool ever touches httpx or JSON directly") but its own client leaks
`httpx.HTTPStatusError` from `raise_for_status()` and `json.JSONDecodeError`
from `resp.json()`; both are wrapped here.
"""


class AnkiError(Exception):
    """Base class for all Anki MCP errors."""


class AnkiNotRunningError(AnkiError):
    """AnkiConnect is unreachable, or reachable but not answering.

    Degraded, not broken. Every message raised as this type must tell the
    caller what to actually do about it — this is the one error the model is
    expected to see routinely and act on.
    """


class AnkiAuthError(AnkiError):
    """AnkiConnect rejected the API key."""


class AnkiConnectError(AnkiError):
    """AnkiConnect returned a non-null `error` field: the action itself failed.

    This is a normal outcome, not a malfunction — a duplicate note, an unknown
    deck, a bad model name all arrive here.
    """


class AnkiProtocolError(AnkiError):
    """The response did not match the expected {result, error} envelope."""
