"""Everything the tools need, built once per process.

Mirrors `document-index-mcp/src/context.ts`. The source spec instantiates its client
at module scope (`anki = AnkiClient()`), which runs at import, gives tests no
seam to inject through, and leaves `close()` defined but never called. Passing
a context explicitly costs one parameter and fixes all three.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .client import AnkiClient
from .config import Config, load_config


@dataclass(frozen=True)
class AppContext:
    config: Config
    anki: AnkiClient

    # Note type name -> the field its cloze template reads, or None for a note
    # type that does not do cloze. `anki_add_note` needs this on every add to
    # catch a deletion-less cloze note before writing it, and asking per card
    # would be a round trip per card in the loop this server is built for —
    # with keep-alive deliberately off, so each one is a fresh handshake. Cached
    # for the life of the process instead: one lookup per note type per run.
    #
    # Deliberately unlike the deck list, which `docs/roadmap.md` refuses to
    # cache. A stale deck list produces a refusal for a deck the user has just
    # created and can see. This cannot go stale the same way: a note type that
    # did not exist when the server started is simply a cache miss, and only
    # changing an existing type's templates in place would be missed — rare
    # enough, and visible enough in Anki, to be worth the fifty round trips.
    #
    # Mutable inside a frozen dataclass on purpose: the binding never changes,
    # only the contents, so `frozen=True` still says what it is there to say.
    cloze_fields: dict[str, str | None] = field(default_factory=dict)


def create_context(config: Config | None = None) -> AppContext:
    resolved = config if config is not None else load_config()
    return AppContext(config=resolved, anki=AnkiClient(resolved))
