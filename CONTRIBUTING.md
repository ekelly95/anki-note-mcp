# Contributing

Issues and pull requests are welcome. This is a small, opinionated server with a
lot of its reasoning written down, so the most useful thing you can do before
changing anything is read why it is the way it is.

## Read first

- **[AGENT_GUIDE.md](AGENT_GUIDE.md)** — the maintained source of truth for the
  product contracts, the deliberate design choices, the layout, and how to
  verify a change. Written for a coding agent, but it is the right briefing for
  a human too.
- **[docs/gotchas.md](docs/gotchas.md)** — the things that look like arbitrary
  style until you know what they cost. Read this before deleting anything that
  looks redundant. Several entries were bugs first.
- **[docs/design.md](docs/design.md)** — thirty rows of deliberate departure
  from the specification this was built from, each with its reasoning and, where
  it was settled by measurement, the measurement.
- **[docs/roadmap.md](docs/roadmap.md)** — the loose ends, what was deferred and
  why, and the review findings that were declined with their reasons. If you are
  about to propose something, check here first — it may already have been
  argued.

## Setup and the verification gate

```bash
git clone https://github.com/ekelly95/anki-note-mcp.git
cd anki-note-mcp
uv sync --locked --group dev
```

Use `--locked`. It is what CI runs, and it fails rather than quietly resolving
when `pyproject.toml` has moved without `uv.lock` following it.

Before opening a pull request, run all five:

```bash
uv run ruff format --check src
uv run ruff check src
uv run mypy src
uv run coverage run -m pytest
uv run coverage report
```

**Never pipe the test run.** A `| tail` that swallows a non-zero exit is how a
red run reads as green.

Branch coverage is gated at 100% and the gate is not negotiable — if a change
adds a line no test reaches, either test it or delete it. Do not lower
`fail_under`.

There is a second, opt-in tier:

```bash
uv run pytest -m live
```

It **writes to a real Anki collection**, so it needs Anki running with
AnkiConnect installed. It creates and deletes its own scratch deck and touches
nothing else, but read `live_test.py` before you run it against a collection you
care about. It is excluded from CI, because there is no Anki on a runner.

## Things that will be turned down

These are settled decisions with reasons recorded, not oversights. Proposing one
is fine if you have new information; proposing one as a cleanup is not:

- **A batch write tool.** `addNotes` reports failures as silent nulls with no
  per-item reason, so a batch cannot say which card failed.
- **A retry on a failed write.** A lost connection cannot be distinguished from
  a lost reply, so retrying `addNote` risks writing the note twice.
- **HTTP keep-alive to AnkiConnect.** Measured: it drops calls part way through
  a loop, which is this server's designed usage.
- **`from __future__ import annotations` in `server.py`.** It turns every tool
  registration into `InvalidSignature`.
- **Raising the `mcp<2` ceiling as routine dependency hygiene.** The cost and
  the trigger for that migration are in `docs/roadmap.md`.

## Tests live beside the code

`client.py` is tested by `client_test.py` in the same directory, not by a
parallel `tests/` tree. New tests go beside the module they cover. Hatchling
excludes `*_test.py` and `testing/` from the wheel, and CI asserts that they
stayed out.

## About the duplicated agent instructions

`AGENTS.md` and `CLAUDE.md` are deliberately byte-identical: different coding
tools look for different filenames, and the content is the same instruction set
either way. If you change one, copy it to the other rather than editing both by
hand — they are meant to stay identical, and diffing them is the check.

## Reporting a vulnerability

Do not open a public issue. See [SECURITY.md](SECURITY.md), which also sets out
what is in scope and what belongs to Anki or AnkiConnect instead.
