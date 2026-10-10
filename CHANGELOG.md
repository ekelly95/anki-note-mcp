# Changelog

Notable changes are listed newest first. Versions follow
[semantic versioning][semver]; releases before `1.0.0` may include breaking
changes.

[semver]: https://semver.org/spec/v2.0.0.html

## 0.2.0 — 2026-10-10

Fixes from an audit on 2026-10-10.

- `anki_update_note` applies the cloze guard. Rewriting a cloze note's text to
  a value with no valid deletion used to be written and reported as
  `updated: true`, and every existing card then rendered "No cloze 1 found on
  card". An update to a cloze field is now judged on the note as it would be
  afterwards and refused if it would be left with no deletion. An update that
  leaves the cloze fields alone is not checked, so a note that is already broken
  can still be edited.
- The cloze guard follows Anki's rules on which notes need a deletion. It
  accepts a deletion in any field the front template reads through a cloze
  filter, not just the first, and names all of those fields in a refusal. It
  applies only to cloze-kind note types, so a standard type whose template uses
  a cloze filter is no longer refused. The note type is read with one
  `findModelsByName` call, cached as before.
- `anki_delete_notes` refuses an `expected_count` above `ANKI_MAX_SEARCH`, the
  most a single search can show the user before they agree to a count. Before
  any request is sent, it points at Anki's Browse window for anything larger.
- `anki_sync` is described as it behaves. The add-on finishes the collection
  sync before replying and raises when a full sync is needed, so success means
  the collection is in step with AnkiWeb. It is not a queued request. Sync now
  waits at least 120 seconds. Its timeout message says the sync may still be
  running and no longer blames a dialog.
- A reply that cannot be decoded (a bad `Content-Encoding`, a redirect loop) is
  an `AnkiProtocolError`. It used to escape the client as a raw httpx exception.
- A `notesInfo` entry missing `fields`, `modelName` or `tags` is a typed error.
  Before, it was read as empty, and `anki_tag_notes` could report a note as
  changed when its state was unknown.
- An `addNote` reply that is not a note ID is a typed error that says the note
  may already exist, so the card is not sent a second time.
- `anki_find_notes` results carry `budget_exhausted`, which says when the
  response budget rather than `limit` cut a search short. A `ANKI_SNIPPET_CHARS`
  that does not fit inside `ANKI_MAX_RESPONSE_CHARS` is refused at startup.
- Snippets and plain text break lines at table cells and `<hr>`, and an image's
  `alt` text is preferred over its filename.
- An `ANKI_CONNECT_API_KEY` of only whitespace is treated as unset.

## 0.1.0 — 2026-10-08

The first release.

- Nine tools over AnkiConnect: check the connection, list decks and note types,
  search, read one note, add one note, update one note, tag many notes, delete
  the notes matching a search, and ask Anki to sync.
- One call per note for adds and edits, deliberately. `addNotes` reports
  failures as silent nulls with no per-item reason, so there is no bulk add and
  each card carries its own outcome.
- `anki_tag_notes`: add or remove tags on many notes in one call. It is
  idempotent and reversible, and it re-reads every note afterwards, so the
  answer names which changed, which do not exist, and which did not take.
- `anki_delete_notes`: permanently delete the notes matching a search. It is
  off unless `ANKI_ALLOW_DELETE` is set. It refuses a blank search, and refuses
  unless the match count equals `expected_count`. The outcome is confirmed by
  re-reading.
- Reads are progressively cheaper than the thing they lead to: search returns
  snippets and never body text, and a whole response is bounded as well as each
  field.
- A field too large to return whole is withheld and named rather than truncated,
  because a truncated value written back would destroy everything past the cap.
- Three independent guards, all safe when unset. `ANKI_READ_ONLY` closes add,
  update, tag, delete and sync together; `ANKI_ALLOW_SYNC` grants sync alone and
  `ANKI_ALLOW_DELETE` grants delete alone, and both are off by default even on a
  writable server.
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
- An `ANKI_CONNECT_URL` with no host or an unusable port (`:abc`, `:99999`) is
  refused at startup. Before, a bad port escaped the client as an untyped httpx
  error on the first call, and a missing host was reported as Anki being closed.
- Leaving out `limit` on `anki_find_notes` no longer goes past `ANKI_MAX_SEARCH`
  when that is set below 20. The default is now clamped to the ceiling.
- The cloze guard reads a note type's template the way Anki does, with the field
  as the last segment of a filter chain. `{{cloze:furigana:Text}}` used to
  refuse correct notes, and `{{edit:cloze:Text}}` turned the guard off.
- Ships `py.typed`, checked by strict mypy. Branch coverage is gated at 100%.
- CI runs Ubuntu on 3.12 for a push or pull request, and the full three-OS,
  five-Python grid on `workflow_dispatch` — see `docs/roadmap.md`. That grid
  passed on Windows, macOS and Linux, 3.10 to 3.14, before this release.

Nothing here changes a card's scheduling or touches a deck, and nothing deletes a
note unless `ANKI_ALLOW_DELETE` is set.
