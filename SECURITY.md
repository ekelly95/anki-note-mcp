# Security

## Reporting a vulnerability

Open a [private security advisory][advisory]. Do not open a public issue for a
suspected vulnerability.

[advisory]: https://github.com/ekelly95/anki-note-mcp/security/advisories/new

This project has no bug bounty. The maintainer aims to reply within two weeks.

## Security model

This is a local stdio MCP server. It speaks JSON-RPC over stdin and stdout to
one host process, and HTTP to AnkiConnect at a configured URL, which is loopback
unless deliberately changed. It has no listening socket of its own, no account,
no database and no application secret.

The thing worth protecting is the collection, and the realistic threats to it are
an agent acting confidently on bad information and a shared deck written by a
stranger. So:

- **There is no destructive tool.** No delete, no deck management, no
  rescheduling. The three writing tools add one note, change one note's fields or
  tags, and ask Anki to sync.
- **Writing is gated, and syncing is gated separately.** `ANKI_READ_ONLY` refuses
  add, update and sync before any of them reaches AnkiConnect. `ANKI_ALLOW_SYNC`
  permits sync alone and is off by default even when writes are allowed, because
  a sync is the only effect that leaves the machine. Unset is the safe value for
  both, and the flags are parsed strictly rather than truthily.
- **Note content is treated as untrusted data.** Every tool that returns content
  says so in the reply, and says it as its own field rather than as delimiters
  around the values — a wrapper the model failed to strip would be written into
  the note on the way back.
- **Reads are bounded in three places**: per snippet, per field, and per whole
  response. Search has no field for raw content in its output model at all, so it
  cannot regress into returning bodies.
- **A field too large to return whole is withheld, not truncated**, and a value
  still carrying the truncation marker is refused on write.
- **Errors do not quote the response.** A malformed reply is described by type,
  size and key names, capped in both count and width, so a protocol failure
  cannot become a channel for note content that every other cap would have
  stopped.
- **Credentials cannot ride in the endpoint URL.** A URL with a username or
  password is refused at startup, and neither that message nor any other echoes
  the value back, because the URL is printed to stderr at launch.

Please report any way to reach the collection past these guards, to make the
server write when `ANKI_READ_ONLY` is set, to make it sync when `ANKI_ALLOW_SYNC`
is not, or to get note content out through an error path.

## Out of scope

- **AnkiConnect and Anki vulnerabilities**, which belong to
  [AnkiConnect](https://git.sr.ht/~foosoft/anki-connect) and
  [Anki](https://github.com/ankitects/anki). This server is a client of an
  endpoint it does not control. AnkiConnect's GitHub repository was archived in
  November 2025 and the project moved to SourceHut; the several GitHub copies
  still turned up by a search are forks, and reporting to one reaches nobody.
- **Anything requiring prior control of the machine.** The collection file is
  readable directly, so an attacker who can run code locally does not need this
  server. That is also why enabling AnkiConnect's own `apiKey` is not treated as
  a control here: it would sit in plaintext in the host's configuration file,
  readable by exactly the processes it is meant to stop.
- **An agent writing a wrong but well-formed card.** These controls bound what a
  mistake can reach, not whether one happens. Keep Anki's own backups on.
- **Content in a shared deck that is merely misleading** rather than a bypass of
  the boundaries above.

## Supported versions

Only the latest release is supported.
