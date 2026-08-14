# Changelog

Notable changes are listed newest first. Versions follow
[semantic versioning][semver]; releases before `1.0.0` may include breaking
changes.

[semver]: https://semver.org/spec/v2.0.0.html

## Unreleased

- `anki_update_note` with an empty `fields` object is now refused instead of
  reported as a success. It used to pass the "nothing to do" check, skip the
  existence and field-name checks below it, and still reach AnkiConnect — which
  looks only for the presence of a `fields` key, so it wrote the note back
  unchanged, moved its modification time and marked it for sync, and the tool
  answered `updated: true`. An empty `tags` list is unaffected: that is still
  the way to clear every tag off a note.
- A `notesInfo` or `modelNames` reply of the wrong shape is now a typed protocol
  error naming the action. Neither can come from a working AnkiConnect, but
  unguarded a string reply used to produce a confident wrong answer rather than
  a failure — "this note does not exist" from a read, an empty result set from
  a search — and in `anki_list_decks_and_models` it would have asked Anki about
  every letter of the reply before failing.
- A malformed environment variable now prints one line and exits, instead of the
  same message underneath a Python stack trace. The messages already named the
  variable, its accepted values and what was found; a host shows you a crashed
  subprocess, so a one-word typo read as a broken server.
- `anki_add_note` no longer reports a wrong field name as an empty note. When
  AnkiConnect refuses a note as "empty" — its only word for it, and the wrong one
  in the common case — the tool now looks up the note type's real field names and
  says which name does not exist, what the names should have been, and that the
  rest of the run will fail identically. A genuinely blank first field says that
  instead, and a refusal it cannot explain reports what it ruled out rather than
  guessing. The lookup costs one extra call, and only on a refusal.
- `anki_add_note` also explains `cannot create note for unknown reason`, which
  the add-on uses for every cloze mismatch and which says nothing at all. It now
  distinguishes a missing deletion, a deletion whose syntax is broken, one in a
  field the card template does not read, and a valid deletion on a note type that
  does not do cloze. The same sentence is used by the guard in the next entry, so
  what a caller reads never depends on which duplicate scope was asked for.
- A cloze note whose deletion is missing, malformed, or in the wrong field is
  now refused before it is written. Under the default `duplicate_scope="deck"`
  AnkiConnect skips its own cloze check, so such a note used to be written,
  reported as created, and render a card reading "No cloze 1 found on card" with
  nothing anywhere saying so. The note type's templates are read once per type
  per run and cached, so a twenty-card run costs one extra call. This is the only
  place the server refuses something the add-on would have accepted; the reasons
  and its deliberate edges are in `docs/roadmap.md`.
- CI runs the full OS and Python-version matrix on `workflow_dispatch` only;
  a push or pull request runs Ubuntu on 3.12. A cost decision about billable
  runner minutes, not a change of intent — see `docs/roadmap.md`.

## 0.1.0

First tagged release. Not published to a package index.

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
- Protocol errors describe a malformed reply by structure and never quote it.
- Ships `py.typed`, checked by strict mypy. Branch coverage is gated at 100%.

Nothing here deletes a note, changes a card's scheduling, or touches a deck.
