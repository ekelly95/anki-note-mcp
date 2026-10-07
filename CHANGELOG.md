# Changelog

Notable changes are listed newest first. Versions follow
[semantic versioning][semver]; releases before `1.0.0` may include breaking
changes.

[semver]: https://semver.org/spec/v2.0.0.html

## Unreleased

- `anki_tag_notes`: add or remove tags on many notes in one call. It is
  idempotent and reversible, and it re-reads every note afterwards, so the
  answer names which changed, which do not exist, and which did not take.
- `anki_delete_notes`: permanently delete the notes matching a search. It is
  off unless `ANKI_ALLOW_DELETE` is set, which is a new switch parsed as
  strictly as the other two. It refuses a blank search, and refuses unless the
  match count equals `expected_count`. The outcome is confirmed by re-reading.
- `ANKI_READ_ONLY` now also closes both new tools.

## 0.1.0 — not yet released

No tag exists and nothing has been published to a package index, so this section
describes what is in the repository rather than what anyone has installed.

- Seven tools over AnkiConnect: check the connection, list decks and note types,
  search, read one note, add one note, update one note, and ask Anki to sync.
- One call per note, deliberately. `addNotes` reports failures as silent nulls
  with no per-item reason, so there is no bulk tool at all and each card carries
  its own outcome.
- Reads are progressively cheaper than the thing they lead to: search returns
  snippets and never body text, and a whole response is bounded as well as each
  field.
- A field too large to return whole is withheld and named rather than truncated,
  because a truncated value written back would destroy everything past the cap.
- Two independent guards, both safe when unset. `ANKI_READ_ONLY` closes add,
  update and sync together; `ANKI_ALLOW_SYNC` grants sync alone, and is off by
  default even on a writable server.
- Anki being closed and Anki being stuck behind a modal dialog are distinct
  answers with distinct remedies, and a lost connection tells the caller to
  retry only where retrying is safe.
- A URL carrying a username or password is refused at startup, because the URL
  is printed in the startup banner and everything sent to it includes note
  content.
- A cloze note whose deletion is missing, malformed, or in a field the card
  template does not read is refused before it is written. Under the default
  `duplicate_scope="deck"` AnkiConnect skips its own cloze check, so such a note
  would otherwise be written, reported as created, and render a card reading
  "No cloze 1 found on card" with nothing anywhere saying so. The note type's
  templates are read once per type per run and cached, so a twenty-card run
  costs one extra call. This is the only place the server refuses something the
  add-on would have accepted; the reasons and its deliberate edges are in
  `docs/roadmap.md`.
- A refusal names the real cause instead of repeating the add-on's word for it.
  AnkiConnect calls a wrong field name an "empty" note, and every cloze mismatch
  "cannot create note for unknown reason"; `anki_add_note` looks up the note
  type's real field names and says which name does not exist, what the names
  should have been, and that the rest of the run will fail identically. Cloze
  mismatches are distinguished four ways — no deletion, broken syntax, a
  deletion in an unread field, and a valid deletion on a note type that does not
  do cloze — and the wording does not depend on which duplicate scope was asked
  for. The lookup costs one extra call, and only on a refusal.
- `anki_update_note` with an empty `fields` object is refused rather than
  reported as a success. AnkiConnect looks only for the presence of a `fields`
  key, so an empty one writes the note back unchanged, moves its modification
  time and marks it for sync. An empty `tags` list is unaffected: that is still
  the way to clear every tag off a note.
- Protocol errors describe a malformed reply by structure and never quote it.
  A `notesInfo` or `modelNames` reply of the wrong shape is a typed error naming
  the action rather than a confident wrong answer — "this note does not exist"
  from a read, an empty result set from a search — and neither can come from a
  working AnkiConnect.
- A malformed environment variable prints one line and exits, rather than the
  same message underneath a stack trace. The message already named the variable,
  its accepted values and what was found; a host shows you a crashed subprocess,
  so a one-word typo would otherwise read as a broken server.
- Ships `py.typed`, checked by strict mypy. Branch coverage is gated at 100%.
- CI runs Ubuntu on 3.12 for a push or pull request, and the full three-OS,
  five-Python grid on `workflow_dispatch` — see `docs/roadmap.md`.

Nothing here deletes a note, changes a card's scheduling, or touches a deck.
