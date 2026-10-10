# Agent guide

The maintained source of truth for anyone — human or model — changing this
repository. `README.md` is for someone using the server; this is for someone
editing it. Where the two disagree, fix both.

Background reading, in this order: `docs/design.md` for why the server is shaped
this way, `docs/gotchas.md` for the things that look like arbitrary style until
you know what they cost, `docs/roadmap.md` for what is already known to be wrong.

## Product contracts

Each of these has at least one test. Breaking one should turn the suite red, and
if it does not, the missing test is the first thing to write.

1. **Only `AnkiError` leaves the client.** No `httpx` exception, no
   `JSONDecodeError`, no `KeyError` reaches a tool. `client_test.py` asserts this
   directly rather than assuming it.
2. **Every request carries `version: 6`,** including every sub-action of a
   `multi`. Without it AnkiConnect answers as API version 4, which has no `error`
   field at all, and every check downstream silently passes.
3. **Every response is validated as a `{result, error}` envelope** before any
   tool sees it.
4. **Anki closed and Anki blocked are different answers with different
   remedies,** and a test asserts the two messages never converge.
5. **Search never returns body text.** The output model for a hit has no field
   for it. This is structural, not conventional, so a refactor cannot regress it.
6. **A missing note is `found: false`, never a crash.** `notesInfo` returns
   `[{}]`, not `[]`.
7. **The tool surface is exactly nine tools,** each with an output schema. A
   tool added without a deliberate decision is scope creep, and `server_test.py`
   is what notices.
8. **A failure arrives as something the model can act on** — an `isError` result
   carrying the message — never as a protocol-level fault.
9. **An oversized field is withheld and named, never truncated,** and a value
   still bearing the truncation marker is refused on write.
10. **`ANKI_READ_ONLY` closes add, update, tag, delete and sync before they
    reach AnkiConnect; `ANKI_ALLOW_SYNC` opens sync and nothing else, and
    `ANKI_ALLOW_DELETE` opens delete and nothing else.** Unset is the safe value
    for all three, and all three are parsed strictly rather than truthily.
11. **A deletion needs an agreed count the user could see.** `anki_delete_notes`
    refuses a blank query, refuses an `expected_count` above
    `ANKI_MAX_SEARCH` before any request is made, and refuses unless the match
    count equals `expected_count`. In every case nothing reaches `deleteNotes`.

## Deliberate choices

Each is a decision plus the failure mode of the obvious alternative.

- **No batch add.** `addNotes` reports failures as nulls with no per-item
  reason, so a batch that half works cannot say which half. The two bulk tools
  that do exist, `anki_tag_notes` and `anki_delete_notes`, are the exception
  because each re-reads every note through `_infos_for` after writing, so the
  answer is per note. Both writes are also idempotent, so a lost reply is safe
  to check and repeat. A bulk tool that cannot do both of those does not belong
  here.
- **No retries, anywhere.** A retry clears neither a modal dialog nor App Nap. It
  also cannot be made safe for `addNote`, because a lost reply is
  indistinguishable from an undelivered request.
- **Keep-alive disabled.** Measured: AnkiConnect closes pooled connections and
  httpx races it, costing 1–5 calls in 60. See `docs/gotchas.md`.
- **Tools are `async def`; the client keeps a sync `invoke` that refuses to run
  on the event loop.** FastMCP calls a synchronous tool directly on the loop, so
  a `def` tool would serialise the whole server behind one slow add. The refusal
  makes every tool test enforce the property, not just the one that measures it.
- **`server.py` has no `from __future__ import annotations`,** and must not gain
  one. Every other module has it, which is what makes its absence look like an
  oversight rather than a load-bearing decision.
- **Config is a factory, not dataclass field defaults.** Field defaults evaluate
  at class-definition time, capturing the environment at import and leaving tests
  no seam.
- **One `AppContext` per process, passed explicitly.** A module-scope client runs
  at import and leaves `close()` defined but never called.
- **Errors describe a malformed response, never quote it.** `_describe` does this
  for requests; `_summarize` is the response side of the same rule, and it exists
  because a 21,000-character reply once went into an error string whole.
- **`updateNote`, not `updateNoteFields`,** which cannot touch tags at all.
- **The preflight is `canAddNotesWithErrorDetail`,** so a duplicate is a result
  rather than an exception a loop has to catch.
- **The cloze guard reads `findModelsByName`,** on add and on any update that
  touches a cloze field. The guard needs two facts: whether the note type is
  cloze-kind, and every field its front templates read. `modelTemplates` gives
  only the second, which made the guard refuse standard types that use a cloze
  filter, and stop at the first cloze field.
- **The duplicate check sets `checkChildren`.** Left at AnkiConnect's default, a
  duplicate in `Deck::Sub` is invisible when adding to `Deck`.
- **The startup banner is ASCII.** Windows stderr is not reliably UTF-8.
- **Tests co-located as `*_test.py`,** beside the module each one covers. Cost:
  an explicit hatchling `exclude` keeps them out of the wheel.
- **No mocks.** `testing/fake_anki.py` is a real HTTP server that mimics
  AnkiConnect faithfully rather than conveniently — it compares the API key on
  every sub-action of a `multi`, and only envelopes successes above version 4.

## Layout

```text
src/anki_mcp/
  __init__.py        __version__, read by hatchling as the one source of truth
  errors.py          AnkiError and its four subclasses; imports nothing local
  config.py          Config + load_config(env); ceilings and floors; URL checks
  context.py         AppContext {config, anki}; the injection seam
  fields.py          HTML to visible text; snippet, truncate, TRUNCATION_SUFFIX
  client.py          the only code that touches httpx or JSON; one choke point
  server.py          the nine tools, their result models, build_server, main
  testing/
    fake_anki.py     a real HTTP server standing in for AnkiConnect
  *_test.py          beside the module each one tests
  live_test.py       opt-in; writes to a real collection via -m live
```

Layering runs one way: `server` → `client` → `{config, errors, fields}`. Nothing
below `client` knows the network exists, and nothing above it touches httpx.

## Verification

Before handing off a change:

```bash
uv run ruff format --check src
uv run ruff check src
uv run mypy src
uv run coverage run -m pytest
uv run coverage report
uv build
```

Never pipe the test run. Branch coverage is gated at 100%: if a change adds a
line no test reaches, either test it or delete it, and do not lower the gate to
make a change pass.

Use `uv run pytest -m live` only when the change touches something a fake cannot
speak for — the add-on's own behaviour. It writes to a real collection.

## Extending the project

- **A new tool** gets its own `register_*(mcp, ctx)` function in `server.py`, its
  own Pydantic result model, and a row in `EXPECTED_TOOLS` in `server_test.py`.
  The test that pins the surface will fail until you add it, which is the point.
  A tool that writes needs `_refuse_if_read_only(ctx)` as its first statement.
- **A new AnkiConnect action** goes through `client.invoke_async`. If the tool
  indexes, slices, iterates or casts the result, type-check it first and raise
  `AnkiProtocolError` — a bare `TypeError` is true but useless to a caller, and
  a string is worse than that, since indexing or iterating one succeeds and
  yields characters. Check it inline unless more than one tool reads the same
  action. `notesInfo` is read by five, which is why `_note_infos` exists. It
  also checks that every real note entry has `fields`, `modelName` and `tags`,
  so a tool can index those without a default that would hide a bad reply.
- **A lookup whose failure must not change the outcome** goes through
  `_lookup_or_none`, which is the only place an `AnkiError` is swallowed
  (`anki_status` aside). Use it only for a courtesy layered on a decision the
  add-on makes anyway, and never on a write.
- **A new configuration value** gets a floor *and* a ceiling in `config.py`, a
  docstring on the `Config` field saying what the number means, a row in the
  README's configuration table, and a test that a malformed value is refused
  rather than coerced.
