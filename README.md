# anki-note-mcp

Write Anki cards from a conversation, one at a time, each with its own answer.

Point an MCP host at this server and an agent can look up your decks and note
types, search your collection, read a note, add one, and edit one — over
AnkiConnect, entirely on your machine. The intended use is the moment a study
session produces something worth remembering: the material is already in the
conversation, and getting it into Anki should not mean leaving, opening Add
Cards, and retyping it.

There is deliberately **no bulk tool**. Twenty cards is twenty calls. Nothing
here deletes a note, empties a deck, or changes a card's scheduling, and syncing
to AnkiWeb is refused unless it is switched on separately.

**Status: beta.** All seven tools work end to end against a real collection. The
offline test suite covers every statement and branch; a separate opt-in tier runs
against a real Anki.

---

## Why another one

There are already several Anki MCP servers, and nearly all of them are thin
wrappers: they take AnkiConnect's actions and expose them one-for-one as tools.
This one is not, and the clearest difference is a bug the others have right now.

**A Cloze note with a missing or malformed deletion gets written silently.**
Under `duplicate_scope="deck"`, which is the default, AnkiConnect skips its own
cloze check — it reports the note as addable, writes it, and returns an
identifier. The note looks ordinary in the browser, and the card it generates
reads *"No cloze 1 found on card."* Any server that simply forwards `addNote` is
producing those cards today. This one reads the note type's templates first and
refuses the write. It is the only place here that overrules the add-on.

The rest follows from the same idea — that a tool should not report a success it
cannot vouch for:

- **No bulk tool.** `addNotes` returns silent nulls, so a batch can say
  "thirty-eight of forty" without being able to say which two. One call per note,
  one answer per note.
- **Bounded output.** Fields are capped per field and per response, and what was
  withheld is named. Forwarding `notesInfo` puts a whole note's HTML into the
  conversation instead.
- **Refusals that name the fault.** AnkiConnect calls a note "empty" when the
  real cause is a field name the note type does not have. This says which key,
  on which note type, and what the real names are.
- **Nothing shortened gets written back.** A value still carrying the truncation
  marker is refused on update, because writing it would silently discard the part
  you never saw.

---

## Why one call per card

The obvious design is a batch tool, and it is the reason agent card-writing kept
failing. AnkiConnect's `addNotes` returns "an array of identifiers … notes that
could not be created will have a null identifier": silent per-item nulls, no
per-item error, and no way to tell which card of forty failed or why. A run that
reports "thirty-eight of forty" and cannot say which two is not usable, because
checking it by hand costs more than writing the cards did.

So each note is its own call and its own answer. Measured against a real
collection: nineteen created, one rejected, and the rejection names itself a
duplicate. The cost is twenty round trips over loopback. What it buys is that a
failure is attributable.

Past roughly fifty cards this stops being the right shape, and a `.apkg`
generator would be the better tool. That is noted in `docs/roadmap.md` rather
than built.

---

## Install

Python 3.10 or newer, [uv](https://docs.astral.sh/uv/), Anki, and the
[AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on (code
**2055492159**).

```bash
git clone https://github.com/ekelly95/anki-note-mcp.git
cd anki-note-mcp
uv sync
```

That creates `.venv` with an `anki-mcp` console script inside it. The path to
that script is what you register with a host, below.

This is not on PyPI. The repository and the package are both `anki-note-mcp`,
because `anki-mcp` on PyPI belongs to a different Anki MCP server. The import
package, the console script and the name this server reports to a host all stay
`anki-mcp` — renaming those would break every registration that already exists
and buy nothing, so the mismatch is deliberate and stops there.

### Register with Claude Desktop

The configuration file is at `%APPDATA%\Claude\claude_desktop_config.json` on
Windows and `~/Library/Application Support/Claude/claude_desktop_config.json` on
macOS.

**Merge into it — never overwrite it.** The same file holds your other settings.

```json
{
  "mcpServers": {
    "anki": {
      "command": "/absolute/path/to/anki-mcp/.venv/bin/anki-mcp",
      "args": []
    }
  }
}
```

On Windows that path ends `\.venv\Scripts\anki-mcp.exe`, and each backslash is
doubled inside JSON.

Use the **absolute** path. A GUI-launched application does not reliably inherit
your shell's PATH, which is also why a bare `uvx` invocation does not work here.
Pointing straight at the console script also avoids a dependency resolution step
on every launch.

Then quit Claude Desktop **completely** and reopen it. On Windows it persists in
the system tray, so closing the window is not enough.

### Register with Codex

```toml
[mcp_servers.anki]
command = "/absolute/path/to/anki-mcp/.venv/bin/anki-mcp"
args = []
startup_timeout_sec = 30
```

### First call

Ask for `anki_status`. It is the one tool designed never to fail: if Anki is
closed, or the add-on is missing, the answer is `connected: false` with a message
saying which, rather than an error.

---

## The seven tools

| Tool | What it does |
|---|---|
| `anki_status` | Is Anki reachable? Never fails — a closed Anki is an answer, not an error. |
| `anki_list_decks_and_models` | Every deck name, and every note type with its field names. Names only, never content. |
| `anki_find_notes` | Search with Anki's own query syntax. Returns note IDs and a short plain-text snippet — never full content. |
| `anki_get_note` | Read ONE note in full. The only tool that returns body text. A field too large to return whole is named, not cut. |
| `anki_add_note` | Add ONE note. Checks for duplicates before writing, so a rejection is a result rather than an exception. |
| `anki_update_note` | Change ONE note's fields and/or tags. Validates field names against the real note first. |
| `anki_sync` | Ask Anki to sync with AnkiWeb. Off unless separately enabled, and says plainly that it confirms nothing. |

The intended order is four steps, and each one is deliberately cheap:

1. **`anki_status`** — confirm Anki is up before doing anything else.
2. **`anki_list_decks_and_models`** — get the real deck names and the real field
   names of the note type you mean to use. Not politeness: see *Troubleshooting*.
3. **`anki_find_notes`** — check whether the card already exists.
4. **`anki_add_note`** — write it, one call per card, and read each answer.

Search cannot return note bodies. That is structural rather than conventional:
the output model for a search hit has a snippet field capped in characters and no
raw field content at all, so a refactor cannot quietly regress it.

---

## Configuration

Entirely environment-driven, and every value has a default. Set these in the
`env` block of the server's entry in your host's configuration.

| Variable | Default | Ceiling | What it does |
|---|---|---|---|
| `ANKI_CONNECT_URL` | `http://127.0.0.1:8765` | — | Where AnkiConnect is listening. |
| `ANKI_CONNECT_API_KEY` | unset | — | Only if you have set `apiKey` in the add-on's own configuration. |
| `ANKI_CONNECT_TIMEOUT` | `10` seconds | 300 | How long to wait for a reply. Connecting has its own 2-second budget. |
| `ANKI_MAX_SEARCH` | `50` | 500 | Most hits one search may return. Also published to clients as the `limit` parameter's maximum. |
| `ANKI_SNIPPET_CHARS` | `120` | 1,000 | Visible characters per search snippet. |
| `ANKI_MAX_FIELD_CHARS` | `5,000` | 100,000 | Largest single field returned whole. Anything above is withheld and named. |
| `ANKI_MAX_RESPONSE_CHARS` | `40,000` | 400,000 | Total note content one call may return. |
| `ANKI_READ_ONLY` | off | — | Refuses every tool that changes anything. |
| `ANKI_ALLOW_SYNC` | off | — | Permits `anki_sync`, and nothing else. |

A malformed value fails loudly at startup rather than silently reverting to the
default — one line on stderr naming the variable, the values it accepts and what
it found, and a non-zero exit — and every numeric one is bounded at both ends:
an extra digit is as much a typo as a missing one. `ANKI_CONNECT_URL` is parsed rather than
prefix-checked, and one carrying a username or password is refused outright:
everything sent to that URL includes your note content, and the URL itself is
printed in the startup banner.

`ANKI_MAX_RESPONSE_CHARS` exists because the per-field caps were not enough on
their own. Forty fields each just inside `ANKI_MAX_FIELD_CHARS` returned 393,236
characters — roughly 98,000 tokens — from a single `anki_get_note`, with nothing
reported as truncated because every field was individually fine.

---

## Safety

- **Nothing here deletes.** There is no delete tool, no deck tool and no
  scheduling tool. The three writing tools add a note, change a note's fields or
  tags, and ask Anki to sync.
- **Two switches guard the collection, and unset is the safe value for both.**
  `ANKI_READ_ONLY=1` closes add, update and sync together, before any of them
  reaches AnkiConnect; the four reading tools are unaffected. `ANKI_ALLOW_SYNC`
  grants sync *only*, and is off by default even on a writable server.
- **Sync is separated from writing on purpose.** An add or an update is a local
  change, visible in Anki and recoverable from a backup. A sync pushes the
  collection to AnkiWeb and on to every other device, which is the one effect
  here that does not stay on this machine.
- **`ANKI_READ_ONLY` is parsed strictly** — `1/true/yes/on` and `0/false/no/off`,
  anything else is a startup error. It is the one setting whose silent
  misreading would leave a collection writable while its owner believed
  otherwise.
- **Note content is data, not instruction.** A shared deck is written by a
  stranger, and a field can carry text that is invisible in Anki. Every tool that
  returns content says so, and says it as a separate field rather than as
  delimiters wrapped around the values — because `fields` has to round-trip
  byte-identical into `anki_update_note`, and a wrapper the model forgot to strip
  would be written into the note.
- **Everything stays on your machine.** The only network traffic is HTTP to
  AnkiConnect at the configured URL, which is loopback unless you change it.

Worth stating plainly: none of this defends the collection against an agent
acting in good faith on bad information. It bounds what a mistake can reach, not
whether one happens. Keep Anki's own backups on.

---

## Troubleshooting

**Every card in a run comes back `created: false`.**
Read the `reason` rather than the `created` flag: six different causes produce
six different wordings. A duplicate says so. A deck or note type that does not
exist names it. A field name the note type does not have was the hard one, because
AnkiConnect refuses such a note as "empty" — a word that sends you looking for
blank content when the fault is in the key. The tool no longer passes that
through: it looks up the note type's real fields and tells you which name is
wrong, what the names should have been, and that the rest of the run will fail
identically until you fix them. Calling `anki_list_decks_and_models` before a run
is still the cheapest way to avoid the mistake, but you no longer need it to
diagnose one. All six wordings are written up in `docs/roadmap.md`.

**A cloze card came out saying "No cloze 1 found on card".**
It was written before 2026-08-14, when this was the largest known defect here:
Anki only refuses these when the duplicate check is scoped to the whole
collection, and this tool scopes it to the deck, so the note was written and
reported as a success with the card quietly broken. `anki_add_note` now reads the
note type's templates and refuses first, naming the field the deletion belongs
in. Clear any already written with Anki's Empty Cards tool. Almost always the
`{{c1::...}}` syntax: one colon instead of two, an upper-case C, a missing brace,
or no deletion at all. Written up in `docs/roadmap.md`.

**An update reported success and changed nothing.**
Check the case of the field names. AnkiConnect matches them case-insensitively
when adding a note and exactly when updating one, so the casing that worked on
the way in can silently do nothing on the way out. `anki_update_note` looks the
real names up first and refuses a name it cannot find, which is what turns this
into a refusal rather than a no-op — but a field written through some other
route will show the original behaviour.

**"Anki may have been closed" while Anki is plainly open.**
Something closed the connection mid-request. If it happens on one call in a long
loop, and you have changed the HTTP limits, put them back: keep-alive is disabled
deliberately, because AnkiConnect closes pooled connections and httpx races it.
If you did not change anything, check whether Anki is showing a dialog.

**Everything hangs, then times out.**
AnkiConnect runs on Anki's GUI thread, so any open modal — Add Cards, a sync
prompt, a confirmation box — stalls every request until it is dismissed. The
message for this case is deliberately different from the closed-Anki one. There
are no retries: a retry clears neither a dialog nor macOS App Nap, it only
doubles the wait.

**A search of a deck you know exists returns nothing.**
A nonexistent deck is not an error. `findNotes` on `deck:Typo` returns an empty
result with no error, so a misspelled deck name is indistinguishable from an
empty deck. Check the name against `anki_list_decks_and_models`.

**`anki_sync` refuses.**
That is correct unless `ANKI_ALLOW_SYNC` is set in this server's environment.
Authoring cards needs neither switch. If it is set and sync still fails with
`sync: auth not configured`, no AnkiWeb account is configured in Anki itself.

**A note came back with a field missing and listed under `truncated`.**
It was larger than `ANKI_MAX_FIELD_CHARS` and has been withheld rather than cut.
This is deliberate — a truncated value written back through `anki_update_note`
would destroy everything past the cap — but it does mean that field cannot be
edited through this server until the cap is raised.

**An edit does not appear to stick.**
Anki's own caveat: a note open in the Browse window may not pick up an external
change.

---

## Development

```bash
uv sync                        # install, including the dev group
uv run ruff format --check src
uv run ruff check src
uv run mypy src
uv run pytest                  # offline; the whole gate below runs against fakes
uv run coverage run -m pytest && uv run coverage report
```

The offline suite covers the envelope and the typed error boundary, both degraded
modes and a test that their messages never converge, HTML normalisation, the
progressive-disclosure guarantees, the exact tool surface and every tool's output
schema, both write guards, concurrency, and the entry point as a real subprocess.
It uses no mocks: `testing/fake_anki.py` is a real HTTP server on an ephemeral
port that mimics AnkiConnect faithfully rather than conveniently, including
comparing the API key on every sub-action of a `multi`.

**Never pipe the test run.** A `| tail` that swallows a non-zero exit is how a
red run reads as green.

Branch coverage is gated at 100%. If a change adds a line no test reaches, either
test it or delete it.

```bash
uv run pytest -m live          # opt-in: writes to a real Anki collection
```

The live tier creates and deletes its own scratch deck and touches nothing else.
It needs Anki running with AnkiConnect installed, and it is excluded from CI for
that reason. It exists to pin add-on behaviour that no fake can be trusted to
predict — the empty-dict-for-a-missing-note shape, the subdeck duplicate rule,
the exact wording of a duplicate rejection and of the three ways a note can be
refused as "empty", and that sixty sequential calls do not drop a connection.

---

## Design notes and limitations

- **[docs/design.md](docs/design.md)** — the three ideas the server is built
  around, and a thirty-row table of every deliberate departure from the
  specification it was built from, with the reasoning and the measurements.
- **[docs/gotchas.md](docs/gotchas.md)** — what bites a user, and separately what
  bites anyone changing the code. Most of them were bugs first, and at least two
  look like arbitrary style until you know what they cost.
- **[docs/roadmap.md](docs/roadmap.md)** — the largest known defect stated
  outright, what each of the six refusal wordings means, the loose ends, what was
  deferred, and the audit findings that were declined with their reasons.

---

## Licence

MIT. See [LICENSE](LICENSE).
