"""The config is injectable, and malformed values fail loudly.

The first test here is a direct regression test for the source spec's
`config.py`, which reads `os.environ` in dataclass field defaults. Those are
evaluated once at class-definition time, so the environment is captured at
import and nothing a test sets afterwards has any effect.
"""

from __future__ import annotations

import pytest

from .config import (
    DEFAULT_URL,
    MAX_FIELD_CHARS,
    MAX_RESPONSE_CHARS,
    MAX_SEARCH_RESULTS,
    MAX_SNIPPET_CHARS,
    MAX_TIMEOUT_S,
    load_config,
)


def test_the_environment_is_a_parameter_not_a_module_import() -> None:
    """The whole point of the factory. If this fails, the config is untestable
    and every cap below it is unverifiable."""
    config = load_config({"ANKI_CONNECT_URL": "http://127.0.0.1:9999"})
    assert config.url == "http://127.0.0.1:9999"

    other = load_config({"ANKI_CONNECT_URL": "http://127.0.0.1:8888"})
    assert other.url == "http://127.0.0.1:8888", "a second call must re-read the environment"


def test_defaults_match_ankiconnects_own() -> None:
    config = load_config({})
    assert config.url == DEFAULT_URL == "http://127.0.0.1:8765"
    assert config.api_key is None
    assert config.timeout_s == 10.0
    assert config.max_search_results == 50
    assert config.snippet_chars == 120
    assert config.max_field_chars == 5000
    assert config.max_response_chars == 40_000


def test_an_empty_api_key_is_treated_as_unset() -> None:
    """AnkiConnect's apiKey is unset by default, and an empty string in a host
    config is far more likely to be a leftover than a real credential."""
    assert load_config({"ANKI_CONNECT_API_KEY": ""}).api_key is None
    assert load_config({"ANKI_CONNECT_API_KEY": "k"}).api_key == "k"


def test_read_only_is_off_unless_it_is_turned_on() -> None:
    assert load_config({}).read_only is False
    assert load_config({"ANKI_READ_ONLY": ""}).read_only is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " on "])
def test_the_obvious_ways_of_writing_yes_all_enable_read_only(value: str) -> None:
    """`raw == "1"` would read ANKI_READ_ONLY=true as writable — a collection
    left open while its owner believes it is closed."""
    assert load_config({"ANKI_READ_ONLY": value}).read_only is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off"])
def test_the_obvious_ways_of_writing_no_all_leave_it_off(value: str) -> None:
    """And `bool(raw)` would read ANKI_READ_ONLY=false as read-only, which is
    the same mistake pointing the other way."""
    assert load_config({"ANKI_READ_ONLY": value}).read_only is False


@pytest.mark.parametrize("value", ["maybe", "2", "please", "y", "off!"])
def test_an_unrecognised_read_only_value_fails_loudly(value: str) -> None:
    """Neither direction is safe to guess, so neither is guessed."""
    with pytest.raises(ValueError, match="ANKI_READ_ONLY"):
        load_config({"ANKI_READ_ONLY": value})


def test_caps_are_read_from_the_environment() -> None:
    config = load_config(
        {
            "ANKI_MAX_SEARCH": "10",
            "ANKI_SNIPPET_CHARS": "40",
            "ANKI_MAX_FIELD_CHARS": "900",
            "ANKI_MAX_RESPONSE_CHARS": "8000",
            "ANKI_CONNECT_TIMEOUT": "2.5",
        }
    )
    assert config.max_search_results == 10
    assert config.snippet_chars == 40
    assert config.max_field_chars == 900
    assert config.max_response_chars == 8000
    assert config.timeout_s == 2.5


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ANKI_MAX_SEARCH", "lots"),
        ("ANKI_MAX_SEARCH", "0"),
        ("ANKI_MAX_SEARCH", "-1"),
        ("ANKI_SNIPPET_CHARS", "3.5"),
        ("ANKI_MAX_FIELD_CHARS", ""),
        ("ANKI_CONNECT_TIMEOUT", "soon"),
        ("ANKI_CONNECT_TIMEOUT", "0"),
        # A fat-fingered extra digit is as much a typo as a missing one, and
        # was accepted in silence until these ceilings existed.
        ("ANKI_MAX_SEARCH", "5000"),
        ("ANKI_SNIPPET_CHARS", "100000"),
        ("ANKI_MAX_FIELD_CHARS", "50000000"),
        ("ANKI_MAX_RESPONSE_CHARS", "lots"),
        ("ANKI_MAX_RESPONSE_CHARS", "0"),
        ("ANKI_MAX_RESPONSE_CHARS", "4000000"),
        ("ANKI_CONNECT_TIMEOUT", "86400"),
    ],
)
def test_a_malformed_value_fails_loudly_rather_than_reverting_to_a_default(
    name: str, value: str
) -> None:
    """A silent fallback hides a typo in a host config until behaviour is
    mysteriously wrong. The empty-string cases are the exception: they are
    indistinguishable from unset and legitimately mean 'use the default'."""
    if value == "":
        assert load_config({name: value}) is not None
        return
    with pytest.raises(ValueError, match=name):
        load_config({name: value})


def test_a_value_at_its_ceiling_is_still_accepted() -> None:
    """The ceilings exist to catch a typo, not to second-guess a deliberate
    choice — so the boundary itself has to be usable. ANKI_MAX_FIELD_CHARS in
    particular is the only lever that makes a large field editable at all,
    since anki_get_note withholds the raw HTML of anything above it."""
    config = load_config(
        {
            "ANKI_MAX_SEARCH": str(MAX_SEARCH_RESULTS),
            "ANKI_SNIPPET_CHARS": str(MAX_SNIPPET_CHARS),
            "ANKI_MAX_FIELD_CHARS": str(MAX_FIELD_CHARS),
            "ANKI_MAX_RESPONSE_CHARS": str(MAX_RESPONSE_CHARS),
            "ANKI_CONNECT_TIMEOUT": str(MAX_TIMEOUT_S),
        }
    )
    assert config.max_search_results == MAX_SEARCH_RESULTS
    assert config.snippet_chars == MAX_SNIPPET_CHARS
    assert config.max_field_chars == MAX_FIELD_CHARS
    assert config.max_response_chars == MAX_RESPONSE_CHARS
    assert config.timeout_s == MAX_TIMEOUT_S


def test_a_rejection_names_the_ceiling_it_broke() -> None:
    """ "must be between 1 and 500" tells the reader what to write instead;
    "invalid value" sends them to the source."""
    with pytest.raises(ValueError, match=str(MAX_SEARCH_RESULTS)):
        load_config({"ANKI_MAX_SEARCH": "5000"})


def test_a_url_without_a_scheme_is_rejected() -> None:
    with pytest.raises(ValueError, match="ANKI_CONNECT_URL"):
        load_config({"ANKI_CONNECT_URL": "127.0.0.1:8765"})


@pytest.mark.parametrize(
    "url",
    [
        "http://user:hunter2@127.0.0.1:8765",
        "http://user@127.0.0.1:8765",
        "https://:hunter2@anki.example.com",
    ],
)
def test_a_url_carrying_credentials_is_rejected(url: str) -> None:
    """AnkiConnect has no use for URL userinfo, and this value is printed.

    `server.main` writes the configured URL to stderr at startup, so a URL of
    this shape would put a password in the log of a server that is otherwise
    careful never to print the API key. Refusing the form at the boundary is
    what makes the banner safe without a redaction helper at every call site.
    """
    with pytest.raises(ValueError, match="ANKI_CONNECT_URL"):
        load_config({"ANKI_CONNECT_URL": url})


def test_the_rejection_does_not_repeat_the_password_back() -> None:
    """The one message that must not name the value it is complaining about.

    Every other rejection in this module quotes the offending value, which is
    what makes it actionable. This one goes to the same stderr as the banner,
    so quoting it would leak exactly what refusing the URL was for.
    """
    with pytest.raises(ValueError) as caught:
        load_config({"ANKI_CONNECT_URL": "http://user:hunter2@127.0.0.1:8765"})
    assert "hunter2" not in str(caught.value)


def test_the_scheme_check_alone_would_not_have_caught_it() -> None:
    """A credential-carrying URL starts with http:// like any other, so this
    is a genuinely separate check rather than a stricter version of one."""
    assert "http://user:hunter2@127.0.0.1:8765".startswith("http://")


@pytest.mark.parametrize(
    "url",
    [
        "http://[oops]/",  # bracketed, but not an address
        "http://[::1/",  # bracket never closed
    ],
)
def test_a_url_the_parser_itself_rejects_fails_at_startup(url: str) -> None:
    """`urlsplit` raises on its own for a malformed host, and that has to
    surface as a refusal rather than as a traceback out of load_config.

    It is reachable only through the host part, since the scheme check runs
    first — which is why both cases here are bracket syntax.
    """
    with pytest.raises(ValueError, match="ANKI_CONNECT_URL"):
        load_config({"ANKI_CONNECT_URL": url})


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:abc",  # not a number
        "http://127.0.0.1:99999",  # out of range
        "http://",  # no host at all
        "http://:8765",  # a port with nothing to attach it to
    ],
)
def test_a_url_with_no_usable_host_or_port_fails_at_startup(url: str) -> None:
    """`urlsplit` accepts all of these without complaint, and then each one
    failed only when a tool ran. A bad port reached httpx as `InvalidURL`, which
    is not a `TransportError`, so it escaped the client untyped and made
    `anki_status` raise. A missing host was reported as "Anki may have been
    closed". Neither says what is actually wrong, which is the config."""
    with pytest.raises(ValueError, match="ANKI_CONNECT_URL"):
        load_config({"ANKI_CONNECT_URL": url})


def test_a_bracketed_ipv6_host_is_still_accepted() -> None:
    """The bracket check exists for patch releases whose parser lets `[oops]`
    through. It must not take real IPv6 loopback down with it."""
    assert load_config({"ANKI_CONNECT_URL": "http://[::1]:8765"}).url == "http://[::1]:8765"


def test_an_unparseable_url_is_not_echoed_back_either() -> None:
    """The same reasoning as the userinfo message, and the easier one to get
    wrong: a URL can be both credential-carrying and unparseable, and this
    branch runs before the userinfo check has had a chance to look."""
    with pytest.raises(ValueError) as caught:
        load_config({"ANKI_CONNECT_URL": "http://user:hunter2@[oops]/"})
    assert "hunter2" not in str(caught.value)


def test_syncing_is_off_unless_it_is_turned_on() -> None:
    """Off by default even on a fully writable server: adds and updates stay on
    this machine, a sync does not."""
    assert load_config({}).allow_sync is False
    assert load_config({"ANKI_ALLOW_SYNC": ""}).allow_sync is False
    assert load_config({"ANKI_READ_ONLY": "0"}).allow_sync is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " on "])
def test_the_obvious_ways_of_writing_yes_all_enable_sync(value: str) -> None:
    assert load_config({"ANKI_ALLOW_SYNC": value}).allow_sync is True


@pytest.mark.parametrize("value", ["maybe", "2", "please", "y", "off!"])
def test_an_unrecognised_sync_value_fails_loudly(value: str) -> None:
    """Same strictness as ANKI_READ_ONLY, and for the same reason: a typo must
    not silently resolve to the more permissive reading."""
    with pytest.raises(ValueError, match="ANKI_ALLOW_SYNC"):
        load_config({"ANKI_ALLOW_SYNC": value})


def test_the_two_collection_switches_are_independent() -> None:
    """Neither implies the other. A read-only server with sync 'allowed' is
    still read-only, and a writable server still cannot sync by itself."""
    both = load_config({"ANKI_READ_ONLY": "1", "ANKI_ALLOW_SYNC": "1"})
    assert both.read_only is True
    assert both.allow_sync is True

    writable = load_config({})
    assert writable.read_only is False
    assert writable.allow_sync is False


def test_deleting_is_off_unless_it_is_turned_on() -> None:
    """Off by default even on a fully writable server, and even with sync on:
    a deletion is the one write here that cannot be undone."""
    assert load_config({}).allow_delete is False
    assert load_config({"ANKI_ALLOW_DELETE": ""}).allow_delete is False
    assert load_config({"ANKI_READ_ONLY": "0", "ANKI_ALLOW_SYNC": "1"}).allow_delete is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " on "])
def test_the_obvious_ways_of_writing_yes_all_enable_delete(value: str) -> None:
    assert load_config({"ANKI_ALLOW_DELETE": value}).allow_delete is True


@pytest.mark.parametrize("value", ["maybe", "2", "please", "y", "off!"])
def test_an_unrecognised_delete_value_fails_loudly(value: str) -> None:
    """The setting where guessing wrong costs the most, so it is the last one
    that should resolve a typo to the permissive reading."""
    with pytest.raises(ValueError, match="ANKI_ALLOW_DELETE"):
        load_config({"ANKI_ALLOW_DELETE": value})


def test_allowing_delete_grants_nothing_else() -> None:
    """ANKI_ALLOW_DELETE opens one tool. It neither reopens a read-only
    collection nor switches sync on."""
    both = load_config({"ANKI_READ_ONLY": "1", "ANKI_ALLOW_DELETE": "1"})
    assert both.read_only is True
    assert both.allow_delete is True
    assert both.allow_sync is False
