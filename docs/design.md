# Design notes

Moved out of the README, which was trying to be three documents at once. This
one is the reasoning: what the server is built around, and every place it
departs from the specification it was built from.

## The three ideas that matter

**One choke point.** Every tool calls `client.invoke()`; no tool touches `httpx`
or JSON. That single method sends `version: 6` on every call, validates the
`{result, error}` envelope, and raises a typed `AnkiError`. Nothing else can
reach the network, so the safety properties are checkable by reading one file —
and they are asserted directly in `client_test.py` rather than assumed.

The version field is load-bearing: omitting it defaults AnkiConnect to API
version 4, and at version 4 "the API response will only contain the value of the
result; no error field is available". Every check downstream would silently pass.

**No batch tool, on purpose.** AnkiConnect's `addNotes` returns "an array of
identifiers … notes that could not be created will have a null identifier":
silent per-item nulls, no per-item error, no way to tell which card of forty
failed or why. Twenty cards is therefore twenty calls, each returning its own
outcome. Measured in `live_test.py`: nineteen created, one rejected, and the
rejection names itself a duplicate. The cost is twenty round trips; the thing
bought is that a failure is attributable.

**Degraded, not broken.** Anki being closed is a designed state with an
actionable message naming the add-on to install, not a stack trace. It is
distinguished from Anki being *open but stuck behind a modal dialog*, which is a
different message because it needs a different action from the user. Two states,
two messages, and one test asserting they never converge.

## Deviations from the source spec

This server was built from a written specification, and the specification is not
part of this repository — the table below is what survives of it, and it is the
more useful half. Every row is a place the build departs from that document on
purpose, with the reason. Nothing here needs the original to be readable.

Rows 1–9 are defects in the document. Rows 10–16 are judgment calls. Rows 17–22
were found by running the thing. Rows 23–30 came out of reviewing it once it
worked, and most of those were settled by reading the add-on's own source rather
than its documentation.

Several rows compare against **the sibling servers**: two other MCP servers by
the same author, written in TypeScript and built either side of this one. They
are referenced only as precedent for a convention — where a habit came from, and
whether departing from it here was deliberate. Nothing in this repository
depends on them, and a host launches every MCP server as its own independent
subprocess regardless of language.

| # | Spec said | Built | Why |
|---|---|---|---|
| 1 | `if duplicate_scope  "deck"` (L277); `if __name__  "__main__"` (L308) | Restored the missing `==` | **The code deliverables do not parse.** Exported from Obsidian with `==highlight==` syntax, which ate `==` inside fenced blocks. Every code block in that document is prose, not paste-ready source. |
| 2 | `@dataclass(frozen=True)` reading `os.environ` in field defaults | `load_config(env=os.environ)` factory | Field defaults evaluate once, at class-definition time, so the environment is captured at import and nothing a test sets afterwards has any effect. Matches `loadConfig(argv = …)` in both sibling servers. |
| 3 | `resp.raise_for_status()`, then `resp.json()` | Both wrapped; `AnkiAuthError` added for 403 | Contradicts the spec's own rule that no tool touches httpx. A bad `apiKey` or a non-JSON body escaped the typed hierarchy as raw `httpx.HTTPStatusError` / `JSONDecodeError`. |
| 4 | Catch `ConnectError`, `ConnectTimeout`, `ReadTimeout` | `ConnectError`/`ConnectTimeout`, then `TimeoutException`, then `TransportError` as backstop | Quitting Anki mid-request raises `RemoteProtocolError`, which is none of the three. |
| 5 | Field values sliced to a character cap | `fields.py` — stdlib HTML normaliser; caps count **visible** characters | Anki fields are HTML. A 120-character window over `<div style="text-align: left; …">` returns no information, defeating the one tool the whole design needs to be cheap. |
| 6 | `anki_get_note`: `if not infos: …` | `_is_real_note()` — also rejects `{}` and a missing `noteId` | **Measured:** `notesInfo(notes=[1])` returns `[{}]`, not `[]`. The list is truthy, the guard passes, and the next line raises `KeyError`. Asserted live so a future change is noticed. |
| 7 | `updateNoteFields` | `updateNote`, with optional `tags` | `updateNoteFields` cannot touch tags at all, making tagging mid-session impossible. Still one tool, one call. |
| 8 | `canAddNotesWithErrorDetail` named as "the clean preflight" | Actually wired as the preflight | The document identifies it and then never uses it. A rejected note now returns `created: false` with a reason and **does not write** — a loop can skip and continue. |
| 9 | `tags: list[str] = []` | `list[str] \| None = None` | Mutable default (ruff `B006`). |
| 10 | `duplicateScope: "deck"`, `checkChildren` left at false | `checkChildren: true` | **Measured:** with the default, a duplicate sitting in `Deck::Sub` is *not* seen when adding to `Deck`. Both halves are asserted live, so if AnkiConnect ever fixes it the deviation is flagged as unnecessary. |
| 11 | `anki_status` returns a dict; other tools raise | Unchanged, but now a verified contract | **Measured:** FastMCP catches a raised exception and returns it as `isError` with the message intact — exactly what `result.ts`'s `fail()` does by hand in the TypeScript siblings. Raising is equivalent *here*; it would not be in the low-level SDK. |
| 12 | `tests/test_client.py` | Co-located `*_test.py`; `testing/fake_anki.py` | Both siblings co-locate tests. Cost: hatchling needs an explicit `exclude` so they stay out of the wheel. |
| 13 | `anki = AnkiClient()` at module scope | `AppContext {config, anki}`, closed on shutdown | Import-time construction gives tests no seam, and left `close()` defined but never called. |
| 14 | No lint or type gate | ruff + mypy `strict` | The siblings' whole quality gate is `tsc --strict`; this is the analogue. ruff caught the `B006` above. |
| 15 | `{"command": "uvx", "args": ["--from", ".", …]}` | Absolute path to the installed console script | **That snippet cannot work.** An MCP host is GUI-launched, so it does not reliably inherit the user PATH, and `.` has no meaningful working directory for the subprocess it spawns. |
| 16 | "Re-pin at implementation time" | Verified against PyPI, 2026-08-05 | `mcp` v1 line is **1.29.0**; **2.0.0** shipped the same day and *is* what a bare install resolves to, so `<2` is load-bearing exactly as claimed. `pydantic` **2.13.4**. `httpx2` **2.9.1** is real and Pydantic-stewarded, but a three-week-old rename is not worth the churn for one synchronous localhost POST. |
| 17 | Tools annotated `-> dict` | Pydantic return models | **Measured:** a bare `-> dict` produces *no* output schema and *no* `structuredContent`; `-> dict[str, Any]` produces an empty schema. Only a model gives callers a typed result. |
| 18 | One `timeout` for the whole request | `connect` budgeted separately at 2s | **Measured:** a connect to a closed `127.0.0.1` port does not refuse, it hangs until timeout. Sharing one budget made "Anki isn't running" — the commonest degraded case there is — take the full 10s. Now ~2s. |
| 19 | `FastMCP("anki-mcp")` | …plus `mcp._mcp_server.version = __version__` | FastMCP's constructor takes no `version`, so clients were told this server's version was the SDK's (`1.29.0`). **Expires upstream:** SDK 2.x takes `MCPServer(name, version=…)`, so this poke retires on migration. |
| 20 | — | No `from __future__ import annotations` in `server.py` | FastMCP builds schemas with `inspect.signature(func, eval_str=True)` against *module globals*. Tool constraints derived from config (`le=max_limit`) are closure variables, so the future import turns every registration into `InvalidSignature`. |
| 21 | `ready — AnkiConnect at …` | ASCII hyphen | Python's stderr on Windows is not reliably UTF-8; a non-encodable character in the banner raises `UnicodeEncodeError` before the transport is up. |
| 22 | `httpx.Client(timeout=…)` | …`limits=Limits(max_keepalive_connections=0)` | **Measured, and the sharpest bug found here.** AnkiConnect closes pooled connections and httpx races it: over 60 rapid sequential calls, keep-alive gave 60/60, 59/60 and 55/60 across three runs, while a fresh connection each time gave 60/60 every run. It surfaced as a loop of `anki_add_note` dying half way with "Anki may have been closed" while Anki was fine — and looping single adds is this server's designed usage, so it is the one pattern that must not be flaky. Retrying would be the wrong fix: a `ReadError` cannot distinguish "never delivered" from "delivered, response lost", so retrying `addNote` risks writing the note twice. |
| 23 | `anki_get_note` caps every field | Oversized raw HTML is **withheld**, and `truncated` names the field | **The cap was a data-loss bug.** It cut raw HTML mid-tag and appended a marker, while `anki_update_note` tells the model to use exactly those values as its editing starting point — so following the instructions destroyed everything past the cap. Measured: a 148-character styled field capped at 60 came back with an unclosed `<div>`, its `<b>` lost with the rest, and `…[truncated]` standing as note content. Withholding the value makes the round trip safe structurally rather than by instruction; `anki_update_note` changes only the fields it is given, so the rest of the note stays editable, and `text` still carries the content for reading. A submitted value still bearing the marker is refused outright, because a shortened copy can reach the tool from somewhere it cannot see. |
| 24 | `AnkiAuthError` on HTTP 403 | …and on the error envelope, with the 403 message corrected | **Read from the installed add-on: the 403 branch was dead.** `handler` raises `Exception('valid api key must be provided')`, catches its own exception, and returns it as an ordinary envelope with **status 200** — so a wrong key arrived as a generic `AnkiConnectError` and the message naming the setting to fix never fired. The only 403 the add-on emits is a CORS rejection, and `allowOrigin` ends in a bare `else: allowed = True`, so an httpx request — which sends no `Origin` header — cannot trigger even that. The branch stays as a backstop for a proxy in front. The key is compared unconditionally, so the message covers both directions: a leftover `ANKI_CONNECT_API_KEY` breaks a default install exactly as hard as a wrong one. |
| 25 | Tools are `def`, client is `httpx.Client` | Tools are `async def`; the client keeps a sync `invoke` and adds `invoke_async` | **Measured:** FastMCP calls a synchronous tool *directly on the event loop* (`func_metadata.py`: `if fn_is_async: … else: return fn(...)`), while the low-level server dispatches every request with `tg.start_soon`. Two concurrent calls against an AnkiConnect stalling 1.0s each took **2.03s**; they now take **1.03s**, and five take 1.05s. What that buys is protocol liveness — pings, `notifications/cancelled` and `tools/list` are answered while a call is in flight, so a 10s add no longer makes the server look dead — *not* Anki throughput, which is serialised on Anki's GUI thread either way. `invoke` now refuses to run on the event loop at all, so every tool test enforces the property rather than only the one that measures it. Also measured: `async def` leaves the generated schemas byte-identical. **Partly expires upstream:** SDK 2.x runs synchronous handlers on worker threads itself, which fixes the defect this worked around. The measurement stands and the design stays — the event-loop refusal is a tested guard, not only an optimisation — but a future reader should know the workaround is no longer the only thing standing between this server and a stalled event loop. |
| 26 | Preflight decides, `addNote` writes | `addNote`'s own duplicate error is caught and reported the same way | The two are separate round trips, so a duplicate can appear between them and arrive as `isError` instead of the `created: false` the tool promises. It stopped being theoretical the moment the tools went async: a `def` tool held the loop, so two adds could not interleave in-process; now they can, and a model looping cards issues calls concurrently. Narrow deliberately — a missing deck or a closed Anki stays loud, so a fifty-card loop halts on a real problem instead of shrugging fifty times. |
| 27 | One `modelFieldNames` call per model | One `multi`, batching them all | 2 + N requests, each a fresh TCP handshake by the no-keep-alive design in row 22, became 2. **Read from the add-on, and the trap is sharper than the batching:** `multi` is `list(map(self.handler, actions))`, and `handler` reads **both `version` and `key` from each sub-action**, not from the envelope. Without `version: 6` a sub-action returns its result bare on success but enveloped on failure — indistinguishable; without `key`, every sub-action fails outright the moment an `apiKey` is configured, which no offline test would have caught if the fake had not been made to check keys recursively. Both halves asserted live. |
| 28 | `to_text` renders every text node | `<style>`/`<script>` contents dropped; `<img>` carries its filename | **Measured:** `to_text('<style>.card { color: red; }</style>Hola')` returned the stylesheet — `html.parser` puts those tags in CDATA mode and hands the raw CSS to `handle_data`. That is the same defect `fields.py` exists to fix, one layer down. The skip flag must not be set from `handle_startendtag`: `<style/>` fires no end tag, so a flag set there swallows the rest of the field — turning a leak into data loss. `[image: wave.png]` because on a vocabulary card the filename is often the meaning; `[sound:]` keeps its bare `[audio]`, since audio usually pronounces text already in the field while an image replaces content that has no other representation. |
| 29 | Caps are checked against a floor | …and a ceiling | Failing loudly ran in one direction only: `ANKI_MAX_FIELD_CHARS=50000000` was accepted in silence. `ANKI_MAX_SEARCH` is also published to every client as `le=` on `anki_find_notes`'s `limit`, so a fat-fingered value shipped a nonsense schema. The ceilings are generous on purpose — `ANKI_MAX_FIELD_CHARS` is now the only lever that makes a large field editable at all. |
| 30 | `finally: ctx.anki.close()` | …plus SIGINT/SIGTERM handlers | Python's default for SIGTERM ends the process outright, so the `finally` never ran; both siblings install handlers for the same reason. Honest limit: on Windows a host stops the server with TerminateProcess and no signal is delivered, so this only helps on POSIX. The path that actually runs there is stdin closing, which `entrypoint_test.py` asserts exits 0 with no traceback. |

## Deviations from the sibling servers

Three, all deliberate, so they are not mistaken for drift.

**Python rather than TypeScript.** Both sibling servers are TypeScript. This one
follows its specification's stack choice, on the grounds that overriding a
document's central premise is a larger departure than any row in the table
above. The interoperability cost really is zero: a host launches each server as
an independent stdio subprocess.

**uv and a lockfile, PEP 735 dependency groups.** The siblings use pnpm and pip
respectively. A `dev` group is not something anyone should be able to install
alongside the package, and `uv.lock` is worth more here than usual — the
`mcp` v1/v2 split landed on a single day, which is exactly the kind of resolve
a lockfile exists to freeze.

**A distribution name that is not the repository name.** See the comment at the
top of `pyproject.toml`.

## Assessed and not adopted

**Code execution with MCP.** The pattern presents a host's tools to a model as a
file tree it explores and writes code against, so intermediate results stay in a
sandbox instead of crossing the context window. It is a **host-side** pattern —
the article describing it says it requires no changes from server authors — and
what it rewards is exactly what this server already is: small tools, no batch
call, and output bounded per snippet, per field and per response, so nothing
large passes through a model on its way somewhere else. Building a code-execution
layer inside a seven-tool server would add a sandbox to defend and change nothing
about what the tools return.

**The 2026-07-28 stateless core.** Written for HTTP deployments behind load
balancers. This server is one stdio subprocess. See `docs/roadmap.md` for where
it stands against that specification, what migrating the SDK would cost, and the
two v2 features that do reach stdio and are still being declined.
