"""Invariants 1-3: the envelope, the typed boundary, and degraded mode.

These are the guarantees the whole design sells. If a raw httpx exception can
reach a tool, the "one choke point" claim is false and every tool's error
handling is built on sand.
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from .client import (
    _LIMITS,
    API_VERSION,
    CONNECT_TIMEOUT_S,
    SYNC_TIMEOUT_S,
    AnkiClient,
    _summarize,
)
from .config import Config
from .errors import (
    AnkiAuthError,
    AnkiConnectError,
    AnkiError,
    AnkiNotRunningError,
    AnkiProtocolError,
)
from .testing.fake_anki import fake_anki, unused_port


def config_for(url: str, *, timeout_s: float = 5.0, api_key: str | None = None) -> Config:
    return Config(
        url=url,
        api_key=api_key,
        timeout_s=timeout_s,
        max_search_results=50,
        snippet_chars=120,
        max_field_chars=5000,
        max_response_chars=40_000,
    )


# --- the happy path, and what goes out on the wire -------------------------


def test_invoke_returns_the_result_field() -> None:
    with fake_anki() as fake:
        fake.on("version", 6)
        with AnkiClient(config_for(fake.url)) as client:
            assert client.invoke("version") == 6


def test_every_call_sends_api_version_6() -> None:
    """Omitting version defaults the add-on to 4, which drops the error field
    from every response and silently disables all validation below."""
    with fake_anki() as fake:
        fake.on("deckNames", ["Default"])
        with AnkiClient(config_for(fake.url)) as client:
            client.invoke("deckNames")
        assert fake.last_request()["version"] == API_VERSION == 6


def test_params_are_forwarded_and_key_omitted_when_unset() -> None:
    with fake_anki() as fake:
        fake.on("findNotes", [1, 2])
        with AnkiClient(config_for(fake.url)) as client:
            client.invoke("findNotes", query="deck:Default")
        request = fake.last_request()
        assert request["params"] == {"query": "deck:Default"}
        assert "key" not in request, "an unset api_key must not be sent at all"


def test_key_is_sent_when_configured() -> None:
    with fake_anki() as fake:
        fake.api_key = "s3cret"
        fake.on("version", 6)
        with AnkiClient(config_for(fake.url, api_key="s3cret")) as client:
            assert client.invoke("version") == 6
        assert fake.last_request()["key"] == "s3cret"


def test_connection_reuse_is_disabled() -> None:
    """AnkiConnect closes pooled connections and httpx races it, which broke a
    loop of adds with a spurious "Anki may have been closed". Measured: 55-60
    of 60 rapid calls survived with keep-alive, 60 of 60 without. The live
    tier hammers the real thing; this just pins the configuration."""
    assert _LIMITS.max_keepalive_connections == 0

    client = AnkiClient(config_for("http://127.0.0.1:8765"))
    try:
        # Reaching into httpx and then httpcore, so degrade to a skip rather
        # than a failure if either moves. The live tier is the real proof.
        pool = getattr(getattr(client._http, "_transport", None), "_pool", None)
        configured = getattr(pool, "_max_keepalive_connections", None)
        if configured is None:
            pytest.skip("httpx internals moved; connection reuse is covered by the live tier")
        assert configured == 0, "the limits were built but never reached the transport"
    finally:
        client.close()


def test_an_injected_http_client_is_used_as_given() -> None:
    """The seam the fake relies on; also means the limits above are the
    client's own concern, not something a caller has to know about."""
    with fake_anki() as fake:
        fake.on("version", 6)
        injected = httpx.Client(timeout=5.0)
        client = AnkiClient(config_for(fake.url), http=injected)
        assert client.invoke("version") == 6
        client.close()


# --- invariant 1: envelope validation --------------------------------------


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("missing error field", '{"result": 6}'),
        ("missing result field", '{"error": null}'),
        ("bare list", "[1, 2, 3]"),
        ("bare string", '"just a string"'),
        ("json null", "null"),
        ("not json at all", "<html>502 Bad Gateway</html>"),
        ("empty body", ""),
    ],
)
def test_a_response_that_is_not_an_envelope_raises_protocol_error(label: str, body: str) -> None:
    with fake_anki() as fake:
        fake.raw_body = body
        with AnkiClient(config_for(fake.url)) as client, pytest.raises(AnkiProtocolError):
            client.invoke("version")


# The size that makes the point: comfortably past the 40,000-character response
# budget a whole tool call is allowed, so a message carrying this has escaped
# every cap the server applies rather than merely being untidy.
_SENTINEL = "Ana's psychiatrist appointment is on the 14th"
_BULK = ("Mitochondrion is the powerhouse of the cell. " * 1200) + _SENTINEL


@pytest.mark.parametrize("size", [1, 40, 1200])
def test_a_malformed_envelope_is_summarised_not_quoted(size: int) -> None:
    """The response side of the rule `_describe` already enforces on requests.

    A malformed reply is still a reply full of note content, and interpolating
    `{data!r}` handed all of it to the model inside an error — past the 40,000
    character response budget, past the promise that only anki_get_note returns
    whole fields, and past the per-field caps as well. An error is not a
    licensed exception to any of those.
    """
    filler = "Mitochondrion is the powerhouse of the cell. " * size
    with fake_anki() as fake:
        # A valid envelope would have `error` too; this is the shape that trips
        # the check while carrying a full notesInfo payload.
        fake.raw_body = json.dumps({"result": [{"fields": {"Front": filler + _SENTINEL}}]})
        client = AnkiClient(config_for(fake.url))
        with client, pytest.raises(AnkiProtocolError) as caught:
            client.invoke("notesInfo")

    message = str(caught.value)
    assert _SENTINEL not in message, "a protocol error leaked note content"
    assert filler[:100] not in message, "a protocol error quoted the response body"
    assert len(message) < 400, f"the error grew with the response: {len(message)} characters"


def test_a_malformed_batch_result_is_summarised_too() -> None:
    """`multi` has its own repr, and it was the larger of the two: this one
    interpolates the whole list of sub-results rather than one object."""
    with fake_anki() as fake:
        fake.raw_body = json.dumps({"result": _BULK, "error": None})
        client = AnkiClient(config_for(fake.url))
        with client, pytest.raises(AnkiProtocolError) as caught:
            client.invoke_multi([("deckNames", {}), ("modelNames", {})])

    message = str(caught.value)
    assert _SENTINEL not in message
    assert len(message) < 400


def test_the_summary_still_says_what_was_wrong_with_the_shape() -> None:
    """Bounded is not the same as useless. For an envelope, the key that is
    missing IS the diagnosis, so the keys are what the summary keeps."""
    with fake_anki() as fake:
        fake.raw_body = '{"result": 6}'
        client = AnkiClient(config_for(fake.url))
        with client, pytest.raises(AnkiProtocolError) as caught:
            client.invoke("version")

    message = str(caught.value)
    assert "'result'" in message, "the keys it did have are the actionable part"
    assert "error" in message, "and the one it was missing is named by the expectation"


def test_response_controlled_keys_cannot_grow_the_summary() -> None:
    """The keys are the one thing the summary quotes, and they come from the
    same untrusted reply as everything else — so they are capped in both count
    and width, or the leak simply moves into the key names."""
    # The sentinel sits at the END of each very long key, so it survives only
    # if keys are quoted whole — which is the thing being ruled out.
    with fake_anki() as fake:
        fake.raw_body = json.dumps({f"field-{i}-" + "x" * 300 + _SENTINEL: i for i in range(200)})
        client = AnkiClient(config_for(fake.url))
        with client, pytest.raises(AnkiProtocolError) as caught:
            client.invoke("version")

    message = str(caught.value)
    assert _SENTINEL not in message, "a long key was quoted whole"
    assert "field-0-" in message, "the readable start of a key is still worth keeping"
    assert "field-50-" not in message, "every key was listed"
    assert len(message) < 800


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "null"),
        (True, "the boolean true"),
        (False, "the boolean false"),
        (7, "a bare int"),
        (7.5, "a bare float"),
        ("abcd", "a string of 4 characters"),
        ([1, 2, 3], "a list of 3 items"),
        ({}, "an empty object"),
        ({"a": 1}, "an object with keys ['a']"),
    ],
)
def test_every_shape_a_response_can_take_is_described_by_structure_alone(
    value: object, expected: str
) -> None:
    """Tested directly rather than through a socket, because the scalar cases
    are the ones a fake AnkiConnect cannot easily be made to send and are
    exactly where a future edit would reach for `{value!r}` again. The contract
    is one sentence: the summary names the type and the size, and quotes no
    value at any point.
    """
    assert _summarize(value) == expected


def test_a_shape_json_cannot_even_produce_is_still_described_and_not_quoted() -> None:
    """The final fallthrough. Nothing arriving from `json.loads` reaches it
    today, which is the whole reason it must not be the branch that quotes:
    a decoder swapped for one returning richer types would silently turn the
    one unbounded case back on.
    """
    assert _summarize({"secret"}) == "a set"


def test_a_non_null_error_field_raises_connect_error_with_the_message() -> None:
    with fake_anki() as fake:
        fake.fails("addNote", "cannot create note because it is a duplicate")
        with AnkiClient(config_for(fake.url)) as client, pytest.raises(AnkiConnectError) as caught:
            client.invoke("addNote", note={})
    assert "duplicate" in str(caught.value)


def test_a_null_result_with_a_null_error_is_returned_not_rejected() -> None:
    """`sync` and several others legitimately return null on success."""
    with fake_anki() as fake:
        fake.on("sync", None)
        with AnkiClient(config_for(fake.url)) as client:
            assert client.invoke("sync") is None


# --- invariant 2: nothing untyped escapes invoke() -------------------------


@pytest.mark.parametrize("status", [400, 404, 500, 502])
def test_an_http_error_status_raises_a_typed_error_not_httpx(status: int) -> None:
    with fake_anki() as fake:
        fake.status_code = status
        fake.raw_body = "upstream exploded"
        with AnkiClient(config_for(fake.url)) as client:
            with pytest.raises(AnkiError) as caught:
                client.invoke("version")
            assert not isinstance(caught.value, httpx.HTTPError)


def test_a_rejected_api_key_arrives_as_an_envelope_not_a_403() -> None:
    """The path that actually fires, and previously did not reach AnkiAuthError.

    Read from the installed add-on: `handler` raises
    Exception('valid api key must be provided'), catches it itself, and returns
    it as an ordinary error envelope with status 200. Matching only on 403 left
    a wrong key arriving as a generic AnkiConnectError, so the message telling
    the user which setting to fix never fired.
    """
    with fake_anki() as fake:
        fake.api_key = "s3cret"
        fake.on("version", 6)
        client = AnkiClient(config_for(fake.url, api_key="wrong"))
        with client, pytest.raises(AnkiAuthError) as caught:
            client.invoke("version")
    message = str(caught.value)
    assert "ANKI_CONNECT_API_KEY" in message, "the message must name the setting to fix"
    assert "valid api key must be provided" in message, "AnkiConnect's own words are dropped"


def test_a_key_sent_to_an_addon_that_wants_none_fails_the_same_way() -> None:
    """The add-on compares `key != apiKey` unconditionally, so a leftover
    ANKI_CONNECT_API_KEY breaks every call against a default install. The
    message has to cover that direction too, not just a wrong key."""
    with fake_anki() as fake:
        fake.on("version", 6)  # fake.api_key stays None, as the add-on ships
        client = AnkiClient(config_for(fake.url, api_key="leftover"))
        with client, pytest.raises(AnkiAuthError) as caught:
            client.invoke("version")
    assert "clear both" in str(caught.value)


def test_an_error_that_merely_quotes_the_api_key_phrase_is_not_an_auth_error() -> None:
    """Why the pattern is anchored. AnkiConnect interpolates caller data into
    other messages, so an unanchored match would tell someone with a mistyped
    deck name to go and fix their API key — worse advice than none."""
    with fake_anki() as fake:
        fake.fails("findNotes", "deck was not found: valid api key must be provided")
        client = AnkiClient(config_for(fake.url))
        with client, pytest.raises(AnkiConnectError) as caught:
            client.invoke("findNotes", query="x")
    assert not isinstance(caught.value, AnkiAuthError)
    assert "deck was not found" in str(caught.value)


def test_an_http_403_blames_the_origin_list_not_the_key() -> None:
    """The only 403 the add-on emits is a CORS origin rejection, with an empty
    body — nothing to do with the key. The branch stays as a backstop, but
    saying "rejected the API key" sent the reader to the wrong setting."""
    with fake_anki() as fake:
        fake.status_code = 403
        fake.raw_body = ""
        client = AnkiClient(config_for(fake.url, api_key="wrong"))
        with client, pytest.raises(AnkiAuthError) as caught:
            client.invoke("version")
    assert "webCorsOriginList" in str(caught.value)


# --- invariant 3: degraded mode is two distinct, actionable states ---------


def test_nothing_listening_says_anki_must_be_running_and_names_the_addon() -> None:
    client = AnkiClient(config_for(f"http://127.0.0.1:{unused_port()}", timeout_s=2.0))
    with client, pytest.raises(AnkiNotRunningError) as caught:
        client.invoke("version")
    message = str(caught.value)
    assert "2055492159" in message, "the message must name the add-on code to install"
    assert "running" in message.lower()


def test_anki_being_closed_is_reported_quickly_even_with_a_long_read_budget() -> None:
    """Connect and read are budgeted separately, and this is why.

    Measured on this machine, a connect to a closed 127.0.0.1 port hangs rather
    than refusing. Sharing one budget would make `anki_status` sit for the full
    read timeout on the single most common degraded case there is.
    """
    client = AnkiClient(config_for(f"http://127.0.0.1:{unused_port()}", timeout_s=30.0))
    started = time.monotonic()
    with client, pytest.raises(AnkiNotRunningError):
        client.invoke("version")
    elapsed = time.monotonic() - started
    assert elapsed < CONNECT_TIMEOUT_S + 2.0, (
        f"took {elapsed:.1f}s to report that Anki is not running, against a "
        f"{CONNECT_TIMEOUT_S}s connect budget"
    )


def test_a_server_that_never_responds_blames_a_modal_dialog() -> None:
    """The read-timeout path. AnkiConnect runs on Anki's GUI thread, so an open
    dialog stalls every request until it is dismissed — a retry cannot clear it,
    so the message has to tell the caller to go and look."""
    with fake_anki() as fake:
        fake.hang_seconds = 5.0
        client = AnkiClient(config_for(fake.url, timeout_s=0.4))
        with client, pytest.raises(AnkiNotRunningError) as caught:
            client.invoke("version")
    message = str(caught.value)
    assert "dialog" in message.lower(), "a read timeout must point at the blocking dialog"
    assert "2055492159" not in message, "this is not the 'install the add-on' case"


# --- sync is answered only once it has finished ----------------------------


def test_sync_waits_longer_than_the_configured_timeout_and_nothing_else_does() -> None:
    client = AnkiClient(config_for("http://127.0.0.1:8765", timeout_s=10.0))
    try:
        assert client.read_budget("sync") == SYNC_TIMEOUT_S
        assert client.read_budget("notesInfo") == 10.0
    finally:
        client.close()

    # A configured timeout above the floor still applies to sync as well.
    client = AnkiClient(config_for("http://127.0.0.1:8765", timeout_s=200.0))
    try:
        assert client.read_budget("sync") == 200.0
    finally:
        client.close()


def test_a_sync_slower_than_the_configured_timeout_still_succeeds() -> None:
    """Read from the add-on: `sync` does not reply until `sync_collection` has
    returned. Held to the shared read budget, a sync of ordinary length timed
    out and was reported as a blocking dialog."""
    with fake_anki() as fake:
        fake.hang_seconds = 0.5
        fake.on("sync", None)
        client = AnkiClient(config_for(fake.url, timeout_s=0.2), sync_timeout_s=3.0)
        with client:
            assert client.invoke("sync") is None


def test_a_sync_that_times_out_says_it_may_still_be_running() -> None:
    with fake_anki() as fake:
        fake.hang_seconds = 5.0
        client = AnkiClient(config_for(fake.url, timeout_s=0.1), sync_timeout_s=0.3)
        with client, pytest.raises(AnkiNotRunningError) as caught:
            client.invoke("sync")
    message = str(caught.value)
    assert "still be running" in message, "the likeliest cause is the sync itself"
    assert "within 0.3s" in message, "it must report the budget it actually used"
    assert "2055492159" not in message, "this is not the 'install the add-on' case"


# --- what a failure tells the caller to do next ----------------------------


def _lost_connection(action: str, *, hang: bool) -> tuple[str, list[str]]:
    """One connection that dies after being established, and its message.

    Both shapes of that: a read timeout (Anki alive but stalled behind a modal
    dialog) and a transport error (Anki quit mid-request). Neither can say
    whether the request was carried out, which is the whole point — so the
    actions the fake actually received come back too.
    """
    with fake_anki() as fake:
        if hang:
            fake.hang_seconds = 5.0
        else:
            fake.close_without_replying = True
        client = AnkiClient(config_for(fake.url, timeout_s=0.4))
        with client, pytest.raises(AnkiNotRunningError) as caught:
            client.invoke(action)
        received = [str(r.get("action")) for r in fake.requests]
    return str(caught.value), received


@pytest.mark.parametrize("hang", [True, False], ids=["read timeout", "transport error"])
def test_a_lost_connection_on_a_read_still_says_retry(hang: bool) -> None:
    """Retrying a read is free, and taking that advice away would be a
    regression in the commonest degraded case there is."""
    message, _ = _lost_connection("notesInfo", hang=hang)
    assert "retry" in message.lower()


@pytest.mark.parametrize("hang", [True, False], ids=["read timeout", "transport error"])
def test_a_lost_connection_on_an_add_does_not_tell_the_caller_to_retry(hang: bool) -> None:
    """The one action where "then retry" was actively wrong.

    `_LIMITS` above already documents that a lost response cannot be told apart
    from an undelivered request, which is exactly why this client performs no
    retries of its own — and then both degraded messages ended by telling the
    model to do it by hand. A note added just before the reply was lost becomes
    two notes, and with allow_duplicate set nothing downstream catches it.
    """
    message, _ = _lost_connection("addNote", hang=hang)
    lower = message.lower()
    assert "may already have been added" in lower, "the outcome is unknown and must say so"
    assert "anki_find_notes" in message, "and it must say how to find out"
    assert "then retry." not in lower, "this is the instruction that caused the double write"


def test_the_add_really_did_reach_anki_before_the_reply_went_missing() -> None:
    """Why the wording matters rather than merely being more cautious.

    The far end received the addNote. Only the reply was lost, and nothing on
    this side can see the difference — so "retry" would have been advice to
    write a second copy of a note that already exists.
    """
    _, received = _lost_connection("addNote", hang=False)
    assert received == ["addNote"], "the fake must have taken delivery for this to be the case"


def test_a_lost_connection_on_a_sync_says_to_look_before_syncing_again() -> None:
    message, _ = _lost_connection("sync", hang=False)
    assert "before syncing again" in message
    assert "then retry." not in message.lower()


def test_an_unreachable_anki_still_says_retry_even_for_an_add() -> None:
    """Not every failure is ambiguous. A connection that was never established
    delivered nothing, so an add is as safe to repeat as a read — treating it
    as unknown would make the ordinary "Anki is closed" case needlessly scary
    in the one loop this server is built for."""
    cfg = config_for(f"http://127.0.0.1:{unused_port()}", timeout_s=2.0)
    with AnkiClient(cfg) as client, pytest.raises(AnkiNotRunningError) as caught:
        client.invoke("addNote", note={})
    assert "retry" in str(caught.value).lower()


# --- batching: one round trip, but the same envelope checks per item -------


def test_a_batch_returns_one_result_per_call_in_order() -> None:
    with fake_anki() as fake:
        fake.on("deckNames", ["Default"])
        fake.on("modelNames", ["Basic", "Cloze"])
        with AnkiClient(config_for(fake.url)) as client:
            results = client.invoke_multi([("deckNames", {}), ("modelNames", {})])
        assert len(fake.requests) == 1, "a batch that costs two round trips is not a batch"
    assert results == [["Default"], ["Basic", "Cloze"]]


def test_every_sub_action_of_a_batch_carries_version_6() -> None:
    """`multi` is `list(map(self.handler, actions))`, and `handler` reads
    `version` from the sub-action, not from the envelope around it. Left at its
    default of 4 a sub-action returns its result bare on success but wrapped on
    failure, so the two become indistinguishable."""
    with fake_anki() as fake:
        fake.on("deckNames", ["Default"])
        with AnkiClient(config_for(fake.url)) as client:
            client.invoke_multi([("deckNames", {})])
        sent = fake.last_request()["params"]["actions"]
    assert [action["version"] for action in sent] == [API_VERSION]


def test_every_sub_action_of_a_batch_carries_the_key() -> None:
    """The key is read from the sub-action for the same reason, so a batch that
    only puts it on the envelope fails outright the moment an apiKey is set —
    and discovery is exactly the tool a caller reaches for first."""
    with fake_anki() as fake:
        fake.api_key = "s3cret"
        fake.on("deckNames", ["Default"])
        with AnkiClient(config_for(fake.url, api_key="s3cret")) as client:
            assert client.invoke_multi([("deckNames", {})]) == [["Default"]]
        sent = fake.last_request()["params"]["actions"]
    assert [action["key"] for action in sent] == ["s3cret"]


def test_a_failing_sub_action_raises_the_same_typed_error_as_a_single_call() -> None:
    """The choke point has to hold per item, or its guarantee covers only the
    unbatched calls."""
    with fake_anki() as fake:
        fake.on("deckNames", ["Default"])
        fake.fails("modelFieldNames", "model was not found: Nope")
        client = AnkiClient(config_for(fake.url))
        with client, pytest.raises(AnkiConnectError) as caught:
            client.invoke_multi([("deckNames", {}), ("modelFieldNames", {"modelName": "Nope"})])
    message = str(caught.value)
    assert "model was not found: Nope" in message
    assert "modelName='Nope'" in message, "with thirty in flight, which one failed matters"


def test_a_batch_error_label_does_not_carry_note_content() -> None:
    """The label names which of thirty calls failed, and that is all it may do.
    A naive repr of the params would paste a whole note's field HTML into a
    message the model reads — the leak anki_find_notes is careful never to
    make, reintroduced through the error path."""
    body = "Mitochondrion is the powerhouse of the cell. " * 20

    with fake_anki() as fake:
        fake.fails("addNote", "cannot create note because it is a duplicate")
        client = AnkiClient(config_for(fake.url))
        with client, pytest.raises(AnkiConnectError) as caught:
            client.invoke_multi([("addNote", {"note": {"fields": {"Front": body}}})])
    assert body not in str(caught.value)


def test_an_empty_batch_costs_no_round_trip() -> None:
    """A collection with no note types is not an error, and asking AnkiConnect
    for nothing is still a handshake."""
    with fake_anki() as fake:
        with AnkiClient(config_for(fake.url)) as client:
            assert client.invoke_multi([]) == []
        assert fake.requests == []


def test_calling_the_blocking_client_from_the_event_loop_is_refused() -> None:
    """The guard that keeps the async conversion true. A future tool that calls
    invoke instead of invoke_async would type-check, pass review and quietly
    pin the server again; this makes every existing tool test catch it."""
    with fake_anki() as fake:
        fake.on("version", 6)
        with AnkiClient(config_for(fake.url)) as client:

            async def body() -> None:
                with pytest.raises(RuntimeError, match="invoke_async"):
                    client.invoke("version")

            asyncio.run(body())


def test_a_batch_that_comes_back_the_wrong_length_is_a_protocol_error() -> None:
    with fake_anki() as fake:
        fake.raw_body = '{"result": [{"result": 1, "error": null}], "error": null}'
        client = AnkiClient(config_for(fake.url))
        with client, pytest.raises(AnkiProtocolError):
            client.invoke_multi([("deckNames", {}), ("modelNames", {})])


# --- the async twins the tools actually call -------------------------------


def test_the_async_twins_return_what_the_synchronous_ones_do() -> None:
    """They exist to move the blocking call off the event loop, not to change
    the contract. That the loop is genuinely freed is asserted in server_test."""
    with fake_anki() as fake:
        fake.on("version", 6)
        fake.on("deckNames", ["Default"])
        with AnkiClient(config_for(fake.url)) as client:

            async def body() -> None:
                assert await client.invoke_async("version") == 6
                assert await client.invoke_multi_async([("deckNames", {})]) == [["Default"]]

            asyncio.run(body())


def test_an_async_failure_is_the_same_typed_error() -> None:
    """A worker thread must not turn AnkiConnectError into something else on
    its way back across the await."""
    with fake_anki() as fake:
        fake.fails("addNote", "cannot create note because it is a duplicate")
        with AnkiClient(config_for(fake.url)) as client:

            async def body() -> None:
                with pytest.raises(AnkiConnectError):
                    await client.invoke_async("addNote", note={})

            asyncio.run(body())


def test_the_two_degraded_messages_are_different() -> None:
    """They are the same exception type, so only the message distinguishes
    'Anki is closed' from 'Anki is open but stuck'. They must not converge."""
    closed = AnkiClient(config_for(f"http://127.0.0.1:{unused_port()}", timeout_s=2.0))
    with closed, pytest.raises(AnkiNotRunningError) as refused:
        closed.invoke("version")

    with fake_anki() as fake:
        fake.hang_seconds = 5.0
        stuck = AnkiClient(config_for(fake.url, timeout_s=0.4))
        with stuck, pytest.raises(AnkiNotRunningError) as hung:
            stuck.invoke("version")

    assert str(refused.value) != str(hung.value)
