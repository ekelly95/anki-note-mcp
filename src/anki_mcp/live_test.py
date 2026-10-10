"""The opt-in tier: the things only a real Anki can prove.

Skipped automatically when nothing is listening on 8765, so a clean checkout
still runs green. Everything here writes to a scratch deck and removes it
afterwards; no test touches a deck it did not create.

Run just these with:  uv run pytest -m live
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Iterator
from dataclasses import replace

import pytest

from .client import AnkiClient
from .config import load_config
from .context import AppContext
from .errors import AnkiAuthError, AnkiConnectError
from .fields import has_cloze_deletion
from .server import (
    _is_cloze_mismatch,
    _is_duplicate,
    _is_empty_note,
    _refuse_broken_cloze,
    build_server,
)

SCRATCH_DECK = "anki-mcp-scratch"
SUBDECK = f"{SCRATCH_DECK}::Verbs"


def anki_is_running() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 8765), timeout=1.0):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not anki_is_running(), reason="Anki is not running on 127.0.0.1:8765"),
]


@pytest.fixture
def anki() -> Iterator[AnkiClient]:
    client = AnkiClient(load_config())
    client.invoke("createDeck", deck=SCRATCH_DECK)
    client.invoke("createDeck", deck=SUBDECK)
    try:
        yield client
    finally:
        # Remove everything this run created, cards included.
        client.invoke("deleteDecks", decks=[SUBDECK, SCRATCH_DECK], cardsToo=True)
        client.close()


def add(anki: AnkiClient, deck: str, front: str, back: str = "b") -> int:
    note_id = anki.invoke(
        "addNote",
        note={
            "deckName": deck,
            "modelName": "Basic",
            "fields": {"Front": front, "Back": back},
            "tags": [],
            "options": {"allowDuplicate": False, "duplicateScope": "collection"},
        },
    )
    return int(note_id)


def test_the_api_version_is_still_6(anki: AnkiClient) -> None:
    assert anki.invoke("version") == 6


def test_an_unknown_note_id_really_does_come_back_as_an_empty_dict(anki: AnkiClient) -> None:
    """The measurement deviation row 6 rests on. If a future AnkiConnect starts
    returning [] instead, this is where we find out."""
    assert anki.invoke("notesInfo", notes=[1]) == [{}]


def test_a_wrong_api_key_is_an_auth_error_not_a_generic_failure() -> None:
    """The measurement the auth mapping rests on, and the tier that was missing
    one entirely.

    A bad key is NOT an HTTP 403: the add-on's `handler` raises, catches its own
    exception and returns it as an ordinary error envelope with status 200. This
    needs no add-on configuration to run, because the key is compared
    unconditionally — sending one to an install that has none fails identically.
    It also pins the exact wording that client.py's anchored pattern matches on:
    if AnkiConnect ever rewords it, this is where we find out.
    """
    cfg = replace(load_config(), api_key="anki-mcp-deliberately-wrong-key")
    with AnkiClient(cfg) as client, pytest.raises(AnkiAuthError) as caught:
        client.invoke("version")
    assert "ANKI_CONNECT_API_KEY" in str(caught.value)


def test_a_batched_call_needs_its_own_version_on_every_sub_action(anki: AnkiClient) -> None:
    """The deviation `invoke_multi` exists for, both halves asserted so that if
    AnkiConnect ever fixes it the extra field is flagged as unnecessary.

    `multi` is `list(map(self.handler, actions))`, and `handler` reads `version`
    from the sub-action rather than from the envelope around it. Left at its
    default of 4 a sub-action's result comes back bare; only at 6 is it wrapped,
    and only a wrapped result makes a per-item failure detectable at all.
    """
    key = load_config().api_key

    def sub_action(**extra: object) -> dict[str, object]:
        action: dict[str, object] = {"action": "deckNames", **extra}
        if key:
            # Read from the add-on for the same reason as the version: without
            # this the sub-action fails auth rather than proving anything.
            action["key"] = key
        return action

    bare, enveloped = anki.invoke("multi", actions=[sub_action(), sub_action(version=6)])

    assert isinstance(bare, list), "an unversioned sub-action now arrives enveloped"
    assert isinstance(enveloped, dict), "a version-6 sub-action must arrive enveloped"
    assert set(enveloped) == {"result", "error"}
    assert enveloped["result"] == bare


def test_the_batched_discovery_path_works_against_the_real_add_on(anki: AnkiClient) -> None:
    """invoke_multi end to end, which is the only place the sub-action shape is
    actually exercised rather than described."""
    models = anki.invoke("modelNames")
    field_names = anki.invoke_multi([("modelFieldNames", {"modelName": m}) for m in models])

    assert len(field_names) == len(models)
    assert all(isinstance(names, list) and names for names in field_names)


def test_a_failing_sub_action_still_raises_through_the_batch(anki: AnkiClient) -> None:
    """The per-item envelope check, against the real thing: one bad call in a
    batch must not come back looking like a successful one."""
    with pytest.raises(AnkiConnectError) as caught:
        anki.invoke_multi([("modelNames", {}), ("modelFieldNames", {"modelName": "no-such-model"})])
    assert "no-such-model" in str(caught.value)


def test_add_note_still_words_a_duplicate_the_way_the_tool_matches_on(
    anki: AnkiClient,
) -> None:
    """The preflight's wording is pinned below; this pins the write's. They are
    the same string today because both come from createNote, but anki_add_note
    now branches on the second one too, so both have to be measured."""
    add(anki, SCRATCH_DECK, "el pájaro")

    with pytest.raises(AnkiConnectError) as caught:
        add(anki, SCRATCH_DECK, "el pájaro")
    assert _is_duplicate(str(caught.value)), (
        f"addNote no longer says 'duplicate': {caught.value!r}. anki_add_note "
        f"reports this as an error rather than a clean skip until the match is fixed."
    )


def test_the_preflight_catches_a_real_duplicate(anki: AnkiClient) -> None:
    add(anki, SCRATCH_DECK, "el perro")

    verdicts = anki.invoke(
        "canAddNotesWithErrorDetail",
        notes=[
            {
                "deckName": SCRATCH_DECK,
                "modelName": "Basic",
                "fields": {"Front": "el perro", "Back": "the dog"},
                "tags": [],
                "options": {"allowDuplicate": False, "duplicateScope": "collection"},
            }
        ],
    )
    assert verdicts[0]["canAdd"] is False
    assert "duplicate" in verdicts[0]["error"].lower()


def verdict_for(anki: AnkiClient, model: str, fields: dict[str, str]) -> dict[str, object]:
    """One preflight verdict, straight from the add-on."""
    result = anki.invoke(
        "canAddNotesWithErrorDetail",
        notes=[
            {
                "deckName": SCRATCH_DECK,
                "modelName": model,
                "fields": fields,
                "tags": [],
                "options": {"allowDuplicate": False, "duplicateScope": "collection"},
            }
        ],
    )
    verdict: dict[str, object] = result[0]
    return verdict


def test_a_field_name_the_model_lacks_is_refused_as_empty(anki: AnkiClient) -> None:
    """The measurement the whole re-wording rests on, and the reason it is
    needed at all: the add-on describes a wrong KEY using the word for blank
    CONTENT. If it ever grows a message of its own, `_explain_empty_note` is
    inventing a problem that no longer exists and should be deleted.
    """
    verdict = verdict_for(anki, "Basic", {"Fron": "el perro", "Back": "the dog"})

    assert verdict["canAdd"] is False, "a field name Basic does not have was accepted"
    error = str(verdict["error"])
    assert _is_empty_note(error), (
        f"a wrong field name no longer says 'empty': {error!r}. anki_add_note passes this "
        f"through unchanged until _is_empty_note matches it."
    )
    assert "Fron" not in error, (
        "the add-on now names the offending field itself, so the re-wording in "
        "_explain_empty_note may be redundant"
    )


def test_a_real_field_left_blank_is_refused_with_the_same_word(anki: AnkiClient) -> None:
    """The case "empty" genuinely describes. Both causes arriving as one string
    is what makes the field list worth fetching: the message can only tell them
    apart by looking, and this pins that they really are indistinguishable."""
    verdict = verdict_for(anki, "Basic", {"Front": "", "Back": "the dog"})

    assert verdict["canAdd"] is False, "a note with a blank first field was accepted"
    assert _is_empty_note(str(verdict["error"]))


def skip_without_cloze(anki: AnkiClient) -> None:
    if "Cloze" not in anki.invoke("modelNames"):
        pytest.skip("this collection has no note type called 'Cloze'")


@pytest.mark.parametrize(
    ("label", "model", "fields"),
    [
        ("no deletion at all", "Cloze", {"Text": "el perro means the dog", "Back Extra": ""}),
        ("broken syntax", "Cloze", {"Text": "el {{c1:perro}}", "Back Extra": ""}),
        ("deletion in the wrong field", "Cloze", {"Text": "el perro", "Back Extra": "{{c1::x}}"}),
        ("a deletion on a non-cloze type", "Basic", {"Front": "el {{c1::pato}}", "Back": "b"}),
    ],
)
def test_a_cloze_mismatch_is_refused_as_for_unknown_reason(
    anki: AnkiClient, label: str, model: str, fields: dict[str, str]
) -> None:
    """The fourth refusal wording, and the reason `_explain_cloze_mismatch`
    exists. AnkiConnect calls all four of these "unknown", which is the least
    useful thing it says anywhere — and it is not unknown at all. If a future
    add-on grows a real message, this is where we find out, and the re-wording
    should be deleted rather than layered on top of a message that now works.
    """
    if model == "Cloze":
        skip_without_cloze(anki)

    verdict = verdict_for(anki, model, fields)

    assert verdict["canAdd"] is False, f"{label}: this is now accepted"
    error = str(verdict["error"])
    assert _is_cloze_mismatch(error), f"{label}: no longer says 'unknown reason': {error!r}"
    assert not _is_empty_note(error), (
        f"{label}: a cloze mismatch now also says 'empty', so the two branches in "
        f"anki_add_note can no longer be told apart"
    )


def test_deck_scope_skips_the_cloze_check_that_collection_scope_applies(
    anki: AnkiClient,
) -> None:
    """Measured 2026-08-14, and the reason `_explain_cloze_mismatch` is only
    reached when a caller asks for collection scope.

    `anki_add_note` defaults to `duplicate_scope="deck"`, which sends
    `duplicateScopeOptions`. Under those options AnkiConnect's preflight answers
    `canAdd: True` for a cloze note with no deletion — the same note it refuses
    outright under collection scope. Two answers from one add-on about one note.
    """
    skip_without_cloze(anki)
    fields = {"Text": "el perro means the dog", "Back Extra": ""}

    def can_add(options: dict[str, object]) -> object:
        return anki.invoke(
            "canAddNotesWithErrorDetail",
            notes=[
                {
                    "deckName": SCRATCH_DECK,
                    "modelName": "Cloze",
                    "fields": fields,
                    "tags": [],
                    "options": options,
                }
            ],
        )[0]["canAdd"]

    assert can_add({"allowDuplicate": False, "duplicateScope": "collection"}) is False
    assert (
        can_add(
            {
                "allowDuplicate": False,
                "duplicateScope": "deck",
                "duplicateScopeOptions": {
                    "deckName": SCRATCH_DECK,
                    "checkChildren": True,
                    "checkAllModels": False,
                },
            }
        )
        is True
    ), "deck scope now applies the cloze check too, so anki_add_note's default path catches it"


def test_a_cloze_note_with_no_deletion_writes_a_card_that_shows_an_error(
    anki: AnkiClient,
) -> None:
    """The consequence of the scope split above, and the whole reason
    `_refuse_broken_cloze` exists.

    Deliberately calls `addNote` directly rather than through the tool, because
    the tool now refuses this — see the test below. What is being pinned is the
    add-on behaviour underneath: left to itself it writes the note and Anki
    renders a card carrying its own error text instead of the content. If this
    ever starts failing because the card comes out fine, the guard has become
    unnecessary and should go.
    """
    skip_without_cloze(anki)

    note_id = anki.invoke(
        "addNote",
        note={
            "deckName": SCRATCH_DECK,
            "modelName": "Cloze",
            "fields": {"Text": "el perro means the dog", "Back Extra": ""},
            "tags": [],
            "options": {
                "allowDuplicate": True,
                "duplicateScope": "deck",
                "duplicateScopeOptions": {
                    "deckName": SCRATCH_DECK,
                    "checkChildren": True,
                    "checkAllModels": False,
                },
            },
        },
    )

    cards = anki.invoke("findCards", query=f"nid:{note_id}")
    assert len(cards) == 1, "a deletion-less cloze note no longer generates a card at all"
    question = anki.invoke("cardsInfo", cards=cards)[0]["question"]
    assert "No cloze" in question, (
        f"the card no longer carries Anki's placeholder, so this may have been fixed "
        f"upstream: {question[-200:]!r}"
    )


def test_the_tool_refuses_the_broken_cloze_note_the_add_on_would_write(
    anki: AnkiClient,
) -> None:
    """The fix, end to end against the real add-on and the real note type.

    Everything above measures AnkiConnect. This measures this server: the same
    note, through `anki_add_note` at its default deck scope, comes back refused
    with a message naming the field and the syntax — and nothing is written.
    """
    skip_without_cloze(anki)
    ctx = AppContext(config=load_config(), anki=anki)

    before = anki.invoke("findNotes", query=f'deck:"{SCRATCH_DECK}"')
    refusal = asyncio.run(
        _refuse_broken_cloze(ctx, "Cloze", {"Text": "el perro means the dog", "Back Extra": ""})
    )

    assert refusal is not None, "the guard no longer catches a deletion-less cloze note"
    assert "'Text'" in refusal, "the real template field is read from the real note type"
    assert "{{c1::" in refusal
    assert anki.invoke("findNotes", query=f'deck:"{SCRATCH_DECK}"') == before

    # And the other half: a valid one is not caught. Without this the guard
    # could refuse everything and still pass the assertion above.
    assert (
        asyncio.run(
            _refuse_broken_cloze(ctx, "Cloze", {"Text": "el {{c1::perro}}", "Back Extra": ""})
        )
        is None
    )
    assert (
        asyncio.run(_refuse_broken_cloze(ctx, "Basic", {"Front": "el perro", "Back": "the dog"}))
        is None
    ), "the guard refused a Basic note, which does no cloze at all"


def test_a_valid_cloze_note_is_accepted(anki: AnkiClient) -> None:
    """The bracket on the test above: without this, a collection in which every
    cloze note were refused would pass it for the wrong reason."""
    skip_without_cloze(anki)

    verdict = verdict_for(anki, "Cloze", {"Text": "el {{c1::perro}} is the dog", "Back Extra": ""})
    assert verdict["canAdd"] is True, f"a valid cloze note was refused: {verdict.get('error')!r}"


def test_find_models_by_name_reports_the_kind_the_guard_keys_off(anki: AnkiClient) -> None:
    """`_cloze_fields` switches the guard on only for a `type` of 1, and reads
    the fronts from `tmpls[].qfmt`. Both are add-on facts, not ours."""
    skip_without_cloze(anki)

    cloze, basic = anki.invoke("findModelsByName", modelNames=["Cloze", "Basic"])
    assert cloze["type"] == 1, "a cloze note type no longer reports type 1"
    assert basic["type"] == 0, "a standard note type no longer reports type 0"
    assert "{{cloze:Text}}" in cloze["tmpls"][0]["qfmt"]


# A note type the live tier never creates, because AnkiConnect has no action
# that deletes one and this tier promises to leave nothing behind. To run the
# test below, make it once in Anki: Tools > Manage Note Types > Add > "Add:
# Cloze", name it this, add a field called `Extra`, and set the card's front
# template to `{{cloze:Text}}<br>{{cloze:Extra}}`.
SCRATCH_CLOZE2 = "anki-mcp-scratch-cloze2"


def test_a_deletion_in_a_second_cloze_field_is_enough_for_anki_and_the_guard(
    anki: AnkiClient,
) -> None:
    """Settles what `_cloze_fields` collecting EVERY cloze field rests on: Anki
    builds cards from a deletion in any field the front reads through a cloze
    filter, not only the first."""
    if SCRATCH_CLOZE2 not in anki.invoke("modelNames"):
        pytest.skip(f"no note type called {SCRATCH_CLOZE2!r}; see the comment above")
    ctx = AppContext(config=load_config(), anki=anki)
    second_only = {"Text": "no deletion here", "Extra": "el {{c1::perro}}"}

    verdict = verdict_for(anki, SCRATCH_CLOZE2, second_only)
    assert verdict["canAdd"] is True, (
        f"Anki refused a deletion in the second cloze field: {verdict.get('error')!r}, so "
        f"accepting one in any cloze field is now too lenient"
    )
    assert asyncio.run(_refuse_broken_cloze(ctx, SCRATCH_CLOZE2, second_only)) is None

    neither = {"Text": "no deletion", "Extra": "none either"}
    assert verdict_for(anki, SCRATCH_CLOZE2, neither)["canAdd"] is False
    refusal = asyncio.run(_refuse_broken_cloze(ctx, SCRATCH_CLOZE2, neither))
    assert refusal is not None
    assert "'Text' or 'Extra'" in refusal


def test_has_cloze_deletion_agrees_with_anki_on_every_marker_variant(anki: AnkiClient) -> None:
    """`_explain_cloze_mismatch` branches on this predicate, so a disagreement
    with the add-on is a confidently wrong message rather than a missing one.

    The missing-brace case is here because the first version of the pattern got
    it wrong: it stopped at the opening marker, so `{{c1::perro}` counted as a
    deletion and the tool would have told the reader that the Cloze note type
    does not do cloze.
    """
    skip_without_cloze(anki)

    variants = [
        "el {{c1::perro}} is the dog",
        "el {{c2::perro}}",
        "{{c12::x}}",
        "el {{c01::perro}}",
        "{{c1::perro::the animal}}",
        "{{c1::}}",
        "{{c1::<b>perro</b>}}",
        "el {{c1::a}} y {{c2::b}}",
        "line one\nel {{c1::perro}}\nline three",
        "el {{c1::perro}}}",
        "el {{c1:perro}}",
        "el {{c::perro}}",
        "el {{C1::perro}}",
        "el {{ c1::perro }}",
        "el {{c1::perro}",
        "el {c1::perro}",
        "{{Front}}",
        "el perro is the dog",
    ]

    disagreed: list[str] = []
    for i, text in enumerate(variants):
        # allowDuplicate, and a unique suffix, so a duplicate verdict can never
        # be mistaken for Anki declining to see a deletion.
        accepted = bool(
            anki.invoke(
                "canAddNotesWithErrorDetail",
                notes=[
                    {
                        "deckName": SCRATCH_DECK,
                        "modelName": "Cloze",
                        "fields": {"Text": f"{text} #{i}", "Back Extra": ""},
                        "tags": [],
                        "options": {"allowDuplicate": True, "duplicateScope": "collection"},
                    }
                ],
            )[0]["canAdd"]
        )
        if accepted != has_cloze_deletion(text):
            disagreed.append(f"{text!r}: anki={accepted}, has_cloze_deletion={not accepted}")

    assert not disagreed, "has_cloze_deletion no longer matches the add-on:\n" + "\n".join(
        disagreed
    )


def test_deck_scope_without_check_children_misses_a_subdeck_duplicate(
    anki: AnkiClient,
) -> None:
    """The measurement behind the checkChildren deviation.

    The same note is added to a subdeck, then offered to the parent. With
    AnkiConnect's defaults the duplicate is NOT seen; with checkChildren it is.
    Both halves are asserted, because the deviation is only worth having if the
    default really does miss it.
    """
    add(anki, SUBDECK, "el gato")

    def can_add(*, check_children: bool) -> bool:
        verdicts = anki.invoke(
            "canAddNotesWithErrorDetail",
            notes=[
                {
                    "deckName": SCRATCH_DECK,
                    "modelName": "Basic",
                    "fields": {"Front": "el gato", "Back": "the cat"},
                    "tags": [],
                    "options": {
                        "allowDuplicate": False,
                        "duplicateScope": "deck",
                        "duplicateScopeOptions": {
                            "deckName": SCRATCH_DECK,
                            "checkChildren": check_children,
                            "checkAllModels": False,
                        },
                    },
                }
            ],
        )
        return bool(verdicts[0]["canAdd"])

    assert can_add(check_children=False) is True, (
        "AnkiConnect's default now catches subdeck duplicates; the checkChildren "
        "deviation may no longer be needed"
    )
    assert can_add(check_children=True) is False, "checkChildren failed to catch the duplicate"


def test_update_note_round_trips_fields_and_tags(anki: AnkiClient) -> None:
    """updateNoteFields, which the source spec specifies, cannot do the tags
    half of this at all."""
    note_id = add(anki, SCRATCH_DECK, "la casa", "the house")

    anki.invoke(
        "updateNote",
        note={
            "id": note_id,
            "fields": {"Back": "the <b>house</b>"},
            "tags": ["spanish", "noun"],
        },
    )

    info = anki.invoke("notesInfo", notes=[note_id])[0]
    assert info["fields"]["Back"]["value"] == "the <b>house</b>"
    assert sorted(info["tags"]) == ["noun", "spanish"]
    assert info["fields"]["Front"]["value"] == "la casa", "an untouched field was modified"


def test_the_add_and_update_paths_disagree_about_field_name_case(anki: AnkiClient) -> None:
    """The external fact anki_update_note's name check exists for.

    `createNote` matches field names case-insensitively, so anki_add_note takes
    any casing; `updateNoteFields` matches exactly, silently drops the rest,
    and returns the same null it returns on success. The casing that worked on
    the way in therefore changes nothing on the way out, and nothing in the
    reply says so — which is why the tool has to look the names up first.

    Pinned live so that if AnkiConnect ever makes the two agree, the check is
    flagged as unnecessary rather than quietly kept forever. Same reasoning as
    the checkChildren pair above.
    """
    note_id = int(
        anki.invoke(
            "addNote",
            note={
                "deckName": SCRATCH_DECK,
                "modelName": "Basic",
                "fields": {"front": "la ventana", "back": "the window"},
                "tags": [],
                "options": {"allowDuplicate": False, "duplicateScope": "collection"},
            },
        )
    )
    added = anki.invoke("notesInfo", notes=[note_id])[0]
    assert added["fields"]["Front"]["value"] == "la ventana", (
        "the add path no longer accepts a lower-cased field name"
    )

    anki.invoke("updateNote", note={"id": note_id, "fields": {"back": "CHANGED"}})

    after = anki.invoke("notesInfo", notes=[note_id])[0]
    assert after["fields"]["Back"]["value"] == "the window", (
        "AnkiConnect now honours a lower-cased name on update, so the name check in "
        "anki_update_note may be unnecessary"
    )


def test_twenty_single_adds_each_report_their_own_outcome(anki: AnkiClient) -> None:
    """The scenario the whole no-batch decision exists for.

    addNotes would return a list of ids with nulls for the failures and no
    reason for any of them. Twenty single calls give twenty answers, and the
    one duplicate in the batch names itself.
    """
    cfg = load_config()
    ctx = AppContext(config=cfg, anki=anki)
    build_server(ctx)  # proves the surface builds against the live configuration

    fronts = [f"palabra-{i:02d}" for i in range(20)]
    fronts[13] = fronts[4]  # a deliberate duplicate part-way through

    created: list[int] = []
    rejected: list[str] = []
    for front in fronts:
        payload = {
            "deckName": SCRATCH_DECK,
            "modelName": "Basic",
            "fields": {"Front": front, "Back": "x"},
            "tags": [],
            "options": {"allowDuplicate": False, "duplicateScope": "collection"},
        }
        verdict = anki.invoke("canAddNotesWithErrorDetail", notes=[payload])[0]
        if not verdict["canAdd"]:
            rejected.append(verdict["error"])
            continue
        created.append(int(anki.invoke("addNote", note=payload)))

    assert len(created) == 19, f"expected 19 adds, got {len(created)}"
    assert len(rejected) == 1, f"expected exactly one rejection, got {rejected}"
    assert "duplicate" in rejected[0].lower()
    assert len(set(created)) == 19, "note ids were not unique"


def test_a_long_run_of_sequential_calls_does_not_drop_a_connection(anki: AnkiClient) -> None:
    """The regression this server's no-keep-alive setting exists for.

    With connection reuse enabled this failed intermittently — 55 to 59 of 60
    — with a ReadError reported as "Anki may have been closed", part way
    through a loop that is the designed usage of this server.
    """
    for i in range(60):
        assert anki.invoke("version") == 6, f"call {i} of 60 failed"


def test_a_note_created_now_gets_a_sane_timestamp_id(anki: AnkiClient) -> None:
    """Anki derives note ids from the system clock, so a machine whose clock is
    wrong writes notes with ids in the future and scheduling follows them there.
    This has happened here once, by about fifteen hours, and nothing else in the
    suite would have noticed."""
    import time

    note_id = add(anki, SCRATCH_DECK, "reloj")
    now_ms = time.time() * 1000
    drift_hours = (note_id - now_ms) / 3_600_000
    assert abs(drift_hours) < 1, f"note id is {drift_hours:.1f}h from now; check the system clock"
