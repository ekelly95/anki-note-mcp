# Roadmap and known limitations

Dated narrative rather than a changelog. `CHANGELOG.md` records what shipped;
this records what is wrong, what was deliberately not built, and what was
declined with its reasoning, so that the next round need not re-argue any of it.

## Fixed: a broken cloze note is no longer written silently

Found on 2026-08-14 while verifying the fix in the section below, and worse than
the defect it was found behind, because nothing reported it at all. The previous
holder of this title told you the wrong cause; this one told you nothing.

**Measured.** `anki_add_note` defaults to `duplicate_scope="deck"`, which sends
`duplicateScopeOptions` to AnkiConnect. Under those options the preflight answers
`canAdd: True` for a Cloze note with no `{{c1::...}}` deletion — the identical
note it refuses under collection scope. The write then succeeds, the tool returns
`created: true` with a note id, and everything about the outcome looks right:

```
collection scope    canAdd=False   cannot create note for unknown reason
deck scope          canAdd=True    (and addNote succeeds)
```

**The card Anki then renders reads:**

```
No cloze ⁨1⁩ found on card.
Please either add a cloze deletion, or use the Empty Cards tool.
```

So a run of twenty cloze cards whose deletions were forgotten, or written as
`{{c1:perro}}` with one colon, reports twenty successes and produces twenty cards
that display an Anki error where the content should be. The note looks fine in
the browser. Nothing in the tool's answer hints at it. This is the exact failure
mode the one-call-per-card design exists to prevent, arriving through a door that
design did not cover — the outcome was attributable, and wrong.

**What now happens.** `_refuse_broken_cloze` runs between the preflight and the
write. It reads the note type's card templates, takes the field named by
`{{cloze:...}}`, and refuses the note if that field carries no valid deletion —
returning the same sentence AnkiConnect's own refusal produces under collection
scope, so what a caller reads never depends on a duplicate-checking option that
has nothing to do with the problem. Three shapes are caught: no deletion,
a deletion whose syntax is broken, and a deletion in a field the template does
not read.

**This is the one place this server overrules the add-on**, so its edges are
deliberate:

- **Read from `modelTemplates`, not `findModelsByName`.** Both were measured and
  both are correct — a cloze note type answers `type: 1` — but the templates say
  *which* field as well as whether, and a custom cloze type need not read its
  first one. Naming the wrong field is the failure this whole area exists to
  remove.
- **Cached per note type for the life of the process**, on `AppContext`. A
  twenty-card run costs one lookup, not twenty, which is what makes the guard
  affordable in a loop where keep-alive is deliberately off. This is not the
  caching `docs/design.md` refuses for the deck list: a note type created after
  startup is a cache miss rather than a stale hit, and only editing an existing
  type's templates in place would be missed.
- **After the preflight**, so a duplicate — the commonest refusal there is —
  never pays for a lookup it does not need. Both facts are pinned by tests.
- **Fails open.** A lookup that errors or answers with the wrong shape means no
  refusal, and the error is not cached. A courtesy check must never become the
  reason a note the add-on would have accepted is turned away.
- **Narrow on purpose.** The opposite direction — cloze markers on a note type
  that does no cloze — is left alone. Measured: that card renders the markers as
  literal text rather than an error, which is ugly, not broken, and refusing it
  would be this server overruling Anki about a card that works. The add-on still
  refuses it under collection scope, and the message for it is still there.

`has_cloze_deletion` in `fields.py` is what the guard branches on, so a
disagreement with Anki would be a confidently wrong refusal rather than a missing
one. It is measured against the add-on across nineteen marker variants, pinned by
a live test that re-runs the whole sweep. It was wrong once: the first pattern
stopped at the opening `{{c1::` and so counted `{{c1::perro}` — one closing brace
— as a deletion, which would have told the reader that the Cloze note type does
not do cloze.

## Fixed: `created: false` no longer says "empty" when it means "wrong name"

Kept in full even though it is fixed, because the measurements behind it are
what any future change here has to respect — and because the section above was
found by chasing them.

`anki_add_note` returns `created: false` with a `reason` whenever the preflight
rejects a note. Duplicate is the case it was designed around, but a field name
that does not exist on the chosen note type produces the same shape: AnkiConnect
treats the note as empty and refuses it.

**Measured against a real collection, 2026-08-13, because earlier notes here
overstated this.** The causes are distinguishable by the reason string, which is
better than had been claimed. The sweep on 2026-08-14 added the fourth and fifth
rows, and the second is the one that made this section necessary:

```
duplicate         cannot create note because it is a duplicate
field typo        cannot create note because it is empty
blank first field cannot create note because it is empty
cloze mismatch    cannot create note for unknown reason
missing deck      deck was not found: <name>
missing note type model was not found: <name>
```

So a caller reading `reason` can tell them apart, and `_is_duplicate`'s
substring match on "duplicate" is correct in every direction. What was wrong was
narrower and still real: **"empty" is a misleading word for "you used a field
name this note type does not have"**. It sent whoever read it looking for blank
content rather than a wrong key, and it named neither the field nor the note
type. A run of twenty cards with one mistyped name reported twenty skips that all
said "empty", which is a cause pointing in the wrong direction.

Also measured the same day: **the add path matches field names
case-insensitively**, so `front` and `Front` both work when adding. The typo that
bites is a name the note type does not have at all, not a case difference — and
the update path is the opposite, matching exactly.

**What it says now.** `_explain_empty_note` catches the empty verdict, spends one
extra call on `modelFieldNames`, and re-words the reason against the real list:

- a name the note type does not have → names the offending key, the note type,
  and every real field name, and says the rest of the run will fail the same way
- every name correct but the first field blank once its HTML is stripped → says
  that instead, and names the field, rather than sending the reader after a typo
  that is not there
- neither → keeps the add-on's own words and reports what has been ruled out,
  rather than guessing at a cause it cannot see

Names are compared lower-cased, because of the case-insensitivity measured
above: a casing difference is not a fault on the add path and must never be
reported as one. That is the opposite of `anki_update_note`, which compares
exactly and offers a did-you-mean — the two messages look similar and are not,
which is why they are deliberately not sharing a helper.

The lookup is on the failure path only. Doing it before every add would cost a
round trip per card in a fifty-card loop, with keep-alive deliberately off so
each one is a fresh handshake, to pre-empt a mistake that is now self-announcing.
`test_the_field_list_is_not_fetched_when_nothing_is_wrong` pins that.

Still a `created: false` result rather than an exception, deliberately. The
tool's docstring promises a structured outcome "so a loop can skip and continue",
and a message that names the fault stops a loop as effectively as a raise without
breaking that contract.

## Status, 2026-08-13

All seven tools are wired and working end to end against a real collection. The
offline suite covers 100% of statements and branches; the live tier is opt-in
because it writes to a real collection.

Landed since the first working version: `ANKI_READ_ONLY` and a separately-gated
`ANKI_ALLOW_SYNC`; a response budget bounding what one call may return in total;
withholding rather than truncating oversized fields; async tools with the sync
client refusing to run on the event loop; batched discovery through `multi`;
structural summaries in protocol errors so a malformed reply can never be quoted
back; and a URL check that refuses embedded credentials.

Landed as engineering rather than behaviour: a licence, a `py.typed` marker,
publishable metadata under the distribution name `anki-note-mcp`, a coverage
gate at the measured 100%, and CI workflows.

**CI is written and has still never completed a job, 2026-08-15.** Actions is
disabled at the repository level, so there is no workflow run history at all.
That is repository state rather than workflow state: nothing about these
workflows can be concluded from it, in either direction. Enabling Actions and
dispatching `ci.yml` is the first thing to do here, and what comes back belongs
in this section.

### What was proven locally instead, 2026-08-14

Everything CI would check that does not require a runner was run by hand, so
that a first green build confirms rather than discovers. Do not repeat this work
without a reason:

- **The whole declared Python range passes.** Both tiers — 264 offline tests at
  a measured 100% of statements and branches, and all 25 live tests against a
  real collection — are green on 3.10, 3.11, 3.12, 3.13 and 3.14. `ruff check`
  and `mypy --strict` are clean on the oldest as well as the newest.
- **`uv sync --locked` succeeds on every one of those versions**, which is the
  install step each matrix job runs, and is what would fail first if `uv.lock`
  had drifted from `pyproject.toml`.
- **Every packaging guard in `release.yml` passes.** `uv build` produces both
  artefacts, `twine check` passes on both, no `_test.py` or `testing/` module
  reaches the wheel, and `py.typed` does. The wheel then installs into a clean
  environment and its `anki-mcp` console script completes an MCP initialize
  handshake, reporting `anki-mcp` 0.1.0 on protocol `2025-11-25`.
- **Both workflow files and `dependabot.yml` parse**, and — the thing most
  likely to break on a first run — the folded `>-` matrix scalars in `ci.yml`
  collapse to single-line strings with no embedded newline, which is exactly the
  failure the comment above them warns about.
- **All five pinned action SHAs resolve to the versions their comments claim**,
  checked against the GitHub API on 2026-08-15: `actions/checkout` v7.0.1,
  `astral-sh/setup-uv` v10.0.0, `actions/upload-artifact` v7.0.1,
  `actions/download-artifact` v8.0.1 and `pypa/gh-action-pypi-publish` v1.14.2.
  A SHA that does not resolve fails at the moment you least want it to, and this
  is cheap to re-run after any pin is bumped:
  `gh api repos/<owner>/<repo>/commits/<tag> --jq .sha`.

So the classifiers' claim now splits cleanly in two. **The Python range is
proven.** **The OS independence is not**, and cannot be from here: every
measurement is on Windows, and neither WSL nor Docker is available on the
machine this was built on. Treat that half as an intention until a matrix run is
green. What a first CI run is still genuinely discovering is Linux and macOS,
and whether GitHub evaluates the `workflow_dispatch` matrix expression the way
reading it says it should.

**The full matrix is on demand rather than on every push.** `ci.yml` picks its
matrix from `github.event_name`: a push or pull request runs Ubuntu on 3.12, and
`workflow_dispatch` runs the whole three-OS, five-version grid. A claim about
which Pythons and which systems work does not change on every push, and one leg
returns in the time the slowest of fifteen would still be running.

That split was originally a cost decision about runner minutes, and what is left
of it is latency — a weaker argument than the one it replaced. **It is worth
re-deciding once the grid has actually run**, rather than kept out of habit: if
fifteen jobs turn out to be fast and stable, put them on every push. Re-prove
the grid with `gh workflow run ci.yml --ref main` after a dependency or
Python-support change, and record the result here.

**Two things about a first run, so they are not read as defects.** The Windows
legs need `shell: bash` on the test step and have it — pwsh does not abort a
script when a native command exits non-zero, so a failing `pytest` followed by a
passing `coverage report` would report green, and the coverage gate would not
catch it because a failing test still executes the code it touches. And two
assertions are wall-clock rather than logical — `server_test.py`'s concurrency
check and `client_test.py`'s connect-timeout bound — so a loaded shared runner
can turn one leg red without anything being wrong. Confirm on a re-run before
investigating.

## Protocol and SDK standing, 2026-08-13

This server runs on **MCP Python SDK 1.29.0** and speaks protocol
**`2025-11-25`** — the newest revision that SDK line knows. The current
specification is **`2026-07-28`**, and **SDK 2.0.0** — released the same day as
1.29.0 — implements it along with every earlier revision.

**Being behind is deliberate, and it is what the SDK itself recommends.** From
the python-sdk README:

> Since `pip install mcp` now installs 2.x, keep a `<2` upper bound on your
> requirement (for example `mcp>=1.28,<2`) until you've migrated.

v1.x lives on its own branch and continues to receive critical bug fixes and
security patches. The deprecation window on the old line is twelve months from
2026-07-28.

**A client from the future is negotiated down, not refused.** Measured against
the running server: a client asking for `2026-07-28` gets `2025-11-25` back and
a successful handshake. Two tests in `entrypoint_test.py` now hold that — one
asserting a future-dated client is still served, one asserting the negotiated
revision equals the SDK's own `LATEST_PROTOCOL_VERSION` rather than a literal,
so an SDK bump moves the expectation instead of breaking the test.

**The trigger.** Revisit when 2.0.x has patch releases behind it, or immediately
if that canary fails — a failure means a host has stopped accepting the
downgrade and migration is no longer optional. Nothing will announce a new SDK
release: `.github/dependabot.yml` covers GitHub Actions only, on purpose,
because the `mcp<2` and `pydantic<3` ceilings are decisions rather than chores.
This is a manual check.

**What migration would cost**, sized so it is a known job rather than a fog:
roughly 106 camelCase field accesses to rename in `server_test.py` (v2 exposes
`is_error`, `input_schema` and friends in snake_case, with camelCase kept on the
wire), nine `FastMCP` annotations and one construction site in `server.py`,
eight places that reach for the private `_mcp_server`, and confirming
`mcp.shared.memory.create_connected_server_and_client_session` survives — it is
what drives the whole tool-surface suite through a real client session. Nothing
architectural is SDK-coupled: the single choke point, the absent batch tool, the
three caps and both write guards would not move. The dependency tree would grow
considerably, though — v2 pulls `starlette`, `uvicorn`, `sse-starlette`,
`python-multipart`, `pyjwt[crypto]`, `opentelemetry-api` and `httpx2` into a
server that speaks stdio to one subprocess and HTTP to one loopback port.

Two things would improve on migration rather than merely change: `MCPServer`
takes a `version` argument, which retires the private-attribute poke currently
flagged in `docs/gotchas.md`; and v2 runs synchronous handlers on worker threads
upstream, which is the defect deviation 25 was written to work around.

### What the 2026-07-28 spec offers, and why none of it is being taken

**The stateless core does not reach this server.** Removing the
`initialize`/`initialized` handshake and `Mcp-Session-Id` exists so a request can
land on any instance behind a load balancer without shared storage. That is an
HTTP concern. This is a stdio server launched as a subprocess by one host, and
there is no second instance for a request to land on.

**Cacheable list results** (`cache_hints=CacheHints(ttl_ms=…, cache_scope=…)`)
reach stdio in v2, and `anki_list_decks_and_models` looks like the obvious
candidate — near-static data, called at the start of every card run. It is not
being taken, and the reason is concrete: a stale deck list produces exactly the
`deck was not found` refusal that wasted six calls on 2026-08-13, and it would
produce it *after* the user had created the deck and could see it in Anki. The
call costs two round trips over loopback. That is the cheaper side of the trade.

**Multi round-trip requests and elicitation** (`Resolve`, `Elicit`,
`InputRequiredResult`) also reach stdio, and would let a write confirm itself
mid-call. Not obviously an improvement here. A caller driving a run of cards can
propose the whole run and take one answer in the conversation, where the user
sees every card at once; per-call protocol prompts would ask six times for what
is currently asked once, and asking more often is not the same as asking better.

**The Tasks extension** is unavailable regardless — 2026-07-28 moved it out of
the core into an official extension the Python SDK does not implement yet.

**Code execution with MCP** was assessed and requires nothing. It is a host-side
pattern — tools presented to a model as a file tree it writes code against, with
intermediate results kept out of the context window — and the Anthropic article
describing it says outright that it needs no changes from server authors. What
it rewards is small, composable tools with bounded output, which is what this
server already is. Recorded here so it is not re-argued.

## Fixed: three defects from a 2026-10-08 audit

Each one was reproduced before it was fixed, and each has a test that failed
first. An `ANKI_CONNECT_URL` with a bad port escaped the client as
`httpx.InvalidURL`, which is not a `TransportError`, and made `anki_status`
raise. A URL with no host read as "Anki may have been closed". Both are now
refused in `_checked_url`. `anki_find_notes` published `default: 20` beside a
smaller `maximum`, and Pydantic does not validate defaults, so an omitted
`limit` went past `ANKI_MAX_SEARCH`. `_CLOZE_TEMPLATE_RE` took everything after
`cloze:` as the field name, so `{{cloze:furigana:Text}}` refused correct notes.
It also required `cloze` to be the first filter, so `{{edit:cloze:Text}}`
disabled the guard. It now follows Anki's rule: the field is the last segment,
and `cloze` may be any filter in the chain.

## Known loose ends

**Snippets come from the first field in model order.** For a Cloze note that is
the cloze text, and for a reversed card it may not be the side you were looking
for. `anki_find_notes` has no way to know which side a caller means.

**The modal-dialog read-timeout path has never been seen against a real Anki
dialog.** It is covered by a fake that hangs. The message is right and the
routing is right, but nobody has watched an actual Add Cards window produce it,
so the wording is unverified against the case it exists for.

**A missing deck is caught by the preflight, so it is skipped rather than
raised — and the test that says otherwise tests a path reality does not take.**
`anki_add_note` has two places that can reject a note. The write-path handler
re-raises anything that is not a duplicate, and
`test_a_non_duplicate_write_failure_is_still_loud` asserts that so a missing deck
stops a run "on the first card" instead of being shrugged off fifty times. But
`canAddNotesWithErrorDetail` catches a missing deck first — `deck was not found:
<name>` is one of the measured preflight reasons above — and that branch
returns `created: false` for every cause alike. So in real use the loud path is
never reached for this case, and the test passes over a branch that only a race
between preflight and write can produce.

Not changed while fixing the "empty" wording, because making the preflight raise
for some causes and not others is a contract change to a tool whose docstring
promises a structured outcome, and it deserves its own decision rather than
arriving as a side effect. The containment is the same one that fixed the field
names: `reason` says exactly which deck was not found, so a caller that reads it
stops on the first card because it understood the message.

**The empty-note measurements in `live_test.py` have not been run.** Three tests
were added on 2026-08-14 to pin what the add-on actually says for a wrong field
name, a genuinely blank field, and a Cloze note with no deletion. Anki was not
running when they were written, so only the first of the three restates a
measurement already taken on 2026-08-13; the other two are predictions until
`uv run pytest -m live` has been run once with Anki open.

**Two tools now swallow `AnkiError` rather than letting it out,** and the
contract is written inline at each rather than extracted. `anki_status` is one,
because a closed Anki is a successful answer to the question it asks.
`_explain_empty_note` is the other, because it only decorates a refusal that has
already been decided, and raising there would convert a structured
`created: false` into an exception a loop suddenly has to catch. A third would be
the point at which this becomes a helper instead of a comment.

**`_LIMITS` bounds `max_keepalive_connections` but not `max_connections`,** so
concurrency is capped only by anyio's 40-thread default. Forty sockets would just
queue inside Anki, which is serialised on its GUI thread regardless — which is
why this is a note rather than a limiter.

## Deferred, in value order

None of these block anything.

1. **An answer-card or review tool**, only if an agent should actually drive
   spaced repetition. Until that is a decision rather than a possibility it is
   scope creep.
2. **Media and TTS**, only with a concrete pronunciation workflow behind it.
3. **A `genanki` `.apkg` generator as a standalone script.** This is the right
   home for true bulk: the moment a request means more than roughly fifty cards,
   the one-call-per-note loop stops being the right shape.
4. **An `.mcpb` bundle.** Assumes a system Python, and is only worth it to hand
   this to someone who does not develop.

## Declined review findings, with reasons

Recorded here so a later round does not re-open them without new information.
Each was raised by a code review of this server, considered, and turned down for
the reason given — which is the part worth keeping. Anything durable those
reviews produced was folded into this file and `docs/gotchas.md` instead of
being kept as a separate backlog, because two documents disagreeing about what
is still open is worse than one.

**Streaming byte ceilings in front of the HTTP buffer.** The buffered body is
this collection, served by this Anki over loopback. The hostile-endpoint version
needs a local port squatter, which already implies arbitrary local code
execution — at which point the collection file is readable directly.

**Idempotency tags on `addNote`.** Plants a reserved tag in real notes, and has
its own check-then-write race that AnkiConnect offers no primitive to close. The
action-aware wording in `_tail` removes the part that was actually wrong, which
was telling the model to retry a call whose outcome is unknowable.

**Validators for all ten AnkiConnect actions.** The guards that exist cover every
result a tool indexes, iterates or casts — `version`, `findNotes`,
`canAddNotesWithErrorDetail`, `notesInfo` and `modelNames`; the last two were
added on 2026-08-14 after a second audit found them missing against this same
rule. The rest would defend against an endpoint that is not AnkiConnect at all,
which this server cannot usefully survive.

**Turning on an AnkiConnect `apiKey`.** It would live in plaintext in the MCP
host's configuration file, readable by exactly the processes it is meant to stop,
and `collection.anki2` is readable directly regardless. Set one if you like, but
not as a control.

**Anchoring `_is_duplicate` to the add-on's full sentence.** The substring match
fails only if a deck or model whose name contains "duplicate" vanishes between
preflight and write. Anchoring trades that for every duplicate becoming a loud
error the moment the add-on rewords its message — caught only when the live tier
next runs. Still declined, and the `created: false` re-wording above is what
addressed the part of it that mattered.

Note that `_is_empty_note` resolves the same trade in the opposite direction, on
purpose, and its docstring says why: a missed duplicate breaks a loop, while a
missed empty verdict merely leaves the add-on's wording in place. Tolerance is
worth a false positive in one case and not the other.

**A deck allowlist and a write audit log.** The entry point both would defend is
imported stranger-authored decks. `ANKI_ALLOW_SYNC` was taken instead, on the
grounds that sync is the only effect here that leaves the machine.

## Declined from the 2026-08-14 code audit, with reasons

A second external audit, code only and explicitly ignoring these documents. It
found the `anki_update_note` empty-fields defect, which is fixed; it re-raised
the validators question, of which two specific instances were in scope and are
now guarded; and it raised three things that should stay as they are. Recorded
in the same spirit as the section above.

**Reading the Back of a card template to find the cloze field.** `_cloze_field`
searches only the `Front` of each template, and the audit called that a defect
on the grounds that a note type with static text on the front and the cloze on
the back is legal Anki. It is not — or rather, it is legal to build and it
generates no cards. The Anki manual, *Card Generation*, says: "Anki looks on the
front template for one or more cloze replacements, like `{{cloze:FieldName}}`.
It then looks in the FieldName field for all cloze references." The back
template takes no part in deciding which cards exist. So `Front` alone is not
narrowness, it is this server holding the same rule Anki holds, which is the
entire job of that function — and searching the `Back` would make it name a
field Anki is not reading, in a refusal, which is the failure the docstring
says it was written to design out.

The residue is a wording one, and small enough to leave: a cloze note type whose
front template has lost its filter is broken in Anki's own terms, and this
server describes it as a note type that "does not build cards from deletions".
True in effect, imprecise in cause, and reachable only under
`duplicate_scope="collection"` on a note type Anki's own template checker
already complains about.

**A friendlier message for a tags-only update of a deleted note.** Real, and
declined on the same grounds as anchoring `_is_duplicate`. A tags-only update
skips the existence lookup on purpose, so the failure arrives as the add-on's
own `Note was not found: <id>` — which already names the note and the problem.
Improving it means either paying for a lookup the skip exists to avoid, or
matching on add-on wording to add one sentence about where to get a current ID.
Neither is worth it for a message that is already true and already specific.

**A size cap on incoming fields.** The reading side is bounded per field and per
response; the writing side is not, so this server can create a note it will
afterwards refuse to hand back whole. That is a real gap and it is now written
down in `docs/gotchas.md` rather than closed. Closing it would mean refusing a
write Anki would accept, for a card that renders perfectly and is merely awkward
for this server to edit — and `_refuse_broken_cloze`, the one place that does
overrule the add-on, declines exactly that trade in its mirror image: cloze
markers on a non-cloze type are left alone because that card is ugly rather than
broken. One override with a stated reason is a position; a second one on weaker
grounds turns it into a habit.
