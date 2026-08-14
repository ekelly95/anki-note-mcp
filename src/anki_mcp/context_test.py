"""The injection seam itself.

`create_context` is two lines and looks too small to test, which is precisely
why it went untested while every module depending on it was covered. It is the
seam the whole design rests on — the reason `AnkiClient` is not built at import
time — so the things worth pinning are that an injected config is honoured, that
its absence falls back to the environment rather than to a default built
somewhere else, and that neither can be swapped afterwards.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from .config import Config
from .context import create_context


def config_for(url: str) -> Config:
    return Config(
        url=url,
        api_key=None,
        timeout_s=1.0,
        max_search_results=5,
        snippet_chars=10,
        max_field_chars=20,
        max_response_chars=30,
    )


def test_an_injected_config_is_the_one_the_client_gets() -> None:
    """If this ever stopped holding, every test that believes it is pointing at
    a fake AnkiConnect would silently be pointing at the real one."""
    cfg = config_for("http://127.0.0.1:9")
    ctx = create_context(cfg)
    try:
        assert ctx.config is cfg
        assert ctx.anki._cfg is cfg
    finally:
        ctx.anki.close()


def test_no_config_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The path a real launch takes. Asserted through values only the
    environment could have supplied, so a hard-coded default would fail it."""
    monkeypatch.setenv("ANKI_CONNECT_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("ANKI_READ_ONLY", "1")

    ctx = create_context()
    try:
        assert ctx.config.url == "http://127.0.0.1:9999"
        assert ctx.config.read_only is True
    finally:
        ctx.anki.close()


def test_the_context_cannot_be_repointed_after_it_is_built() -> None:
    """Frozen on purpose: every tool closes over one context, so a mutable
    `config` would let any one of them redirect every other tool's traffic."""
    ctx = create_context(config_for("http://127.0.0.1:9"))
    try:
        with pytest.raises(FrozenInstanceError):
            ctx.config = config_for("http://evil.example:80")  # type: ignore[misc]
    finally:
        ctx.anki.close()
