# Gotchas worth knowing

Most of these were bugs first. They are split into the ones that bite a person
using the server and the ones that bite a person changing it — the second group
is not guessable from the code, and at least two of them will look like
arbitrary style until you know what they cost.

## Using it

- **Anki must be running, with no dialog open.** AnkiConnect lives on Anki's GUI
  thread, so an open modal — Add Cards, a sync prompt, a confirmation box —
  stalls every request until it is dismissed. The client reports this distinctly
  from "Anki is closed", but it cannot clear it for you. There are deliberately
  **no retries**: a retry clears neither a dialog nor macOS App Nap, it just
  doubles the wait.

- **`anki_sync` waits for the sync, and refuses a full one.** This was
  documented as fire-and-forget until 2026-10-10, which it never was. Read from
  the installed add-on: its `sync` handler runs the collection sync to the end
  before replying, raises unless the outcome was "no changes" or a normal sync,
  and only then starts Anki's own follow-up sync from the main window, media
  included, which the call does not wait for. So success means the collection is
  in step with AnkiWeb; a sync that needs a full upload or download comes back as
  `Sync status … not one of …` and syncs nothing; and with no AnkiWeb account it
  fails with `sync: auth not configured`. Because it blocks for the real
  duration, sync gets a read budget of at least 120 seconds rather than the
  shared `ANKI_CONNECT_TIMEOUT`, and its timeout message says the sync may still
  be running rather than blaming a dialog.

- **`anki_sync` refusing is usually correct, not a regression.** It is gated
  behind `ANKI_ALLOW_SYNC`, separately from write access, and that variable is
  unset by default even on a writable server. Authoring cards needs neither
  switch set.

- **A nonexistent deck is not an error.** `findNotes` on `deck:Typo` returns
  `{"result": [], "error": null}`, so a misspelled deck is indistinguishable from
  an empty one. The tool description tells the model to check names via
  `anki_list_decks_and_models` when a search unexpectedly returns nothing.

- **`created: false` means more than "duplicate", and the reason now says which
  in plain terms.** A field name the note type does not have makes AnkiConnect
  treat the note as empty and refuse it, so it comes back `created: false` like a
  duplicate does. Measured 2026-08-13: the reason strings differ, `cannot create
  note because it is a duplicate` against `cannot create note because it is
  empty`. The trap was the word "empty", which sent you looking for blank content
  instead of a wrong key and named neither the field nor the note type. The tool
  no longer passes that through: it looks the note type's real fields up and says
  which name is wrong, what the names actually are, and that the rest of the run
  will fail the same way. You can still read the names up front from
  `anki_list_decks_and_models`, but you no longer have to in order to diagnose
  this. See `roadmap.md` for what each of the six refusal wordings means.

- **A cloze note with a missing or broken deletion is refused, and that refusal
  is this server's, not Anki's.** Measured 2026-08-14: because `anki_add_note`
  defaults to `duplicate_scope="deck"`, AnkiConnect's own preflight never applies
  its cloze check, so `{{c1:perro}}` with one colon — or no deletion at all —
  used to give you `created: true` and a card reading *"No cloze 1 found on
  card"*, with the note looking perfectly normal in the browser. The tool now
  reads the note type's templates and refuses first, naming the field the
  deletion belongs in. If you are hunting cards written before this landed,
  Anki's Empty Cards tool finds them. See `roadmap.md`.

- **A note aimed at a deck that does not exist is refused, not filed.**
  AnkiConnect does not create the deck on the way in, and this server has no
  deck tool at all — it does not manage decks. The refusal is the clearest of
  them all: `deck was not found: <name>`. Create the deck in Anki first, or
  with an explicit `createDeck` against AnkiConnect, before a run.

- **`tags` on `anki_update_note` replaces, it does not append.** Read the note
  first if you mean to add one.

- **A field larger than `ANKI_MAX_FIELD_CHARS` cannot be edited through this
  server at all.** Its raw HTML is withheld rather than truncated, which is the
  point — a truncated value written back destroys everything past the cap — but
  it is a real capability gap. The escape hatch is raising the cap.

  Nothing bounds the write side, so this server can create a note it will then
  refuse to hand back: `anki_add_note` will store a field of any size, and every
  later read withholds it. That asymmetry is deliberate — refusing an oversized
  write would be this server overruling Anki about a card that works perfectly,
  which it does in exactly one place and for a card that does not (see the cloze
  entry above) — but it is worth knowing before you paste a transcript into a
  card. Fixing one is done in Anki itself, or by raising the cap.

- **Note IDs are millisecond timestamps from the system clock.** A machine whose
  clock is fast creates notes with future IDs and skewed due dates, and nothing
  in Anki will flag it. `live_test.py` asserts a new note's ID lands within an
  hour of now, which is the cheapest available canary.

- **Editing a note that is open in Anki's Browse window may not stick.** Anki's
  own caveat, not this server's.

## Changing it

- **Never add `from __future__ import annotations` to `server.py`.** FastMCP
  builds tool schemas with `inspect.signature(func, eval_str=True)`, resolved
  against *module globals*. Several tool constraints are derived from config
  (`le=max_limit`) and are therefore closure variables, which module globals do
  not contain — so the future import turns every registration into
  `InvalidSignature` at import time. Every other module in the package has it,
  which is exactly what makes its absence here look like an oversight.

- **Keep-alive must stay off.** `_LIMITS` sets `max_keepalive_connections=0`
  deliberately. AnkiConnect closes pooled connections and httpx races it:
  measured over 60 rapid sequential calls, keep-alive gave 60/60, 59/60 and 55/60
  across three runs, while a fresh connection each time gave 60/60 every run. The
  symptom is a loop of `anki_add_note` dying half way with "Anki may have been
  closed" while Anki is visibly fine — and looping single adds is this server's
  designed usage, so it is the one pattern that must not be flaky.

- **Never answer a lost connection by retrying an `addNote`.** A `ReadError`
  cannot distinguish "never delivered" from "delivered, response lost". `_tail`
  varies the closing sentence of the two degraded messages by action for this
  reason: everything else here is idempotent, and `addNote` is not. This is why
  there is no retry layer rather than a configurable one.

- **Every sub-action of a `multi` needs its own `version` and `key`.** Read from
  the add-on: `multi` is `list(map(self.handler, actions))`, and `handler` reads
  both fields from each sub-action, not from the envelope. Without `version: 6` a
  sub-action returns its result bare on success but enveloped on failure —
  indistinguishable. Without `key`, every sub-action fails the moment an `apiKey`
  is configured. `testing/fake_anki.py` checks keys recursively so an offline
  test can catch this; a convenient fake would not have.

- **`notesInfo` returns `[{}]` for a missing note, not `[]`.** The obvious guard,
  `if not infos`, passes on a truthy list of one empty dict and the next line
  raises `KeyError`. `_is_real_note` is a `TypeGuard` that also rejects a missing
  `noteId`.

- **Add and update disagree about field-name case.** `createNote` matches field
  names case-insensitively, so any casing gets a note in. `updateNoteFields`
  matches exactly, silently drops the fields it did not match, and returns the
  same `null` it returns on success — so the casing that worked on the way in
  changes nothing on the way out and nothing in the reply says so. This is the
  external fact `anki_update_note`'s name check exists for, and it is pinned live
  so that if AnkiConnect ever makes the two agree, the check is flagged as
  unnecessary rather than quietly kept forever.

- **A bad API key arrives as HTTP 200, not 403.** The add-on's `handler` raises
  its own exception, catches it, and returns it as an ordinary error envelope.
  The only 403 it emits is a CORS rejection, and `allowOrigin` ends in a bare
  `else: allowed = True`, so an httpx request — which sends no `Origin` header —
  cannot trigger even that. The 403 branch stays as a backstop for a proxy in
  front of Anki, and is dead against the add-on itself.

- **The opaque-tag flag must not be set from `handle_startendtag`.** `<style/>`
  opens and closes at once and fires no end tag, so a flag set there is never
  cleared and swallows the rest of the field — turning a stylesheet leak into
  data loss. `handle_startendtag` returns early for opaque tags and delegates
  everything else, which is also why `<br/>` and `<img/>` behave like their open
  forms.

- **The startup banner is deliberately ASCII.** Python's stderr on Windows is not
  reliably UTF-8, and a non-encodable character there raises `UnicodeEncodeError`
  before the transport is even up — a startup crash caused entirely by a banner.

- **`mcp._mcp_server.version` is a private-attribute poke and is load-bearing.**
  FastMCP's constructor takes no `version`, so without it clients are told this
  server's version is the SDK's. A test pins it, which is what will notice if the
  attribute moves.

- **`anki_status` must never raise.** It is the one tool whose failure is a
  successful answer, and the contract is currently written inline rather than
  extracted. Anything added to it belongs inside the `try`.

- **Driving this server from Python on Windows needs `encoding="utf-8"`
  explicitly.** `subprocess.Popen(..., text=True)` decodes the pipe with the
  locale codec, which is cp1252 here, and a tool result carrying a curly quote
  or a `√` raises `UnicodeDecodeError` on a reply the server wrote perfectly.
  The same applies to printing one: `sys.stdout.reconfigure(encoding="utf-8")`.
  Both were hit while verifying real cards, and neither is a defect in the
  server — but both look like one for the first minute.

- **Do not pipe the test run.** The suite is the gate, and a `| tail` that
  swallows a non-zero exit is how a red run reads as green.

- **`uv run` fails while an MCP host has this server registered and open.** Any
  `uv run` re-syncs the environment first, which means replacing
  `.venv\Scripts\anki-mcp.exe` — and the host is holding it, so Windows refuses
  with `os error 32` and the command never starts. It looks like a broken
  toolchain and is not one. Either close the host or use `uv run --no-sync`,
  which skips the reinstall; the console script is irrelevant to the test suite,
  which imports the package. `uv lock` and `uv build` are unaffected.

- **`mypy` can refuse to start on a Windows machine with Application Control.**
  The mypy wheel is compiled with mypyc, and a Smart App Control or WDAC policy
  can block its `.pyd` files from loading, which makes `uv run mypy src` die on
  import before it has read a line. Like `os error 32` above, it looks like a
  broken toolchain and is not one. Run the pure-Python build of the same version
  instead, pointed at the project's interpreter so it still sees the installed
  packages and the strict settings in `pyproject.toml`:
  `uvx --no-binary-package mypy --from mypy==<locked version> mypy
  --python-executable .venv\Scripts\python.exe src`. Checked on 2026-10-10 with
  mypy 2.3.0: it loads `build.py` rather than the compiled module, and gives the
  same result as the compiled one. An `uv run --with mypy` overlay does not work
  for this, because the project's compiled copy is found first.
