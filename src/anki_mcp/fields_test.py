"""Invariant 5: a snippet is worth reading.

Anki fields are HTML. The source spec caps them with a plain character slice,
which at 120 characters can be consumed entirely by a single opening `<div>`
tag. That defeats the tool the whole progressive-disclosure design depends on
being cheap.
"""

from __future__ import annotations

import pytest

from .fields import (
    TRUNCATION_SUFFIX,
    has_cloze_attempt,
    has_cloze_deletion,
    snippet,
    to_text,
    truncate,
)


def test_tags_are_stripped() -> None:
    assert to_text("<div>Hola</div>") == "Hola"
    assert to_text('<span style="color: red">rojo</span>') == "rojo"


def test_entities_are_unescaped_and_nbsp_becomes_a_real_space() -> None:
    """&nbsp; unescapes to U+00A0, which looks like a space but is not one —
    it breaks length accounting and naive matching downstream."""
    assert to_text("a&nbsp;b") == "a b"
    assert "\xa0" not in to_text("a&nbsp;b")
    assert to_text("&amp; &lt;tag&gt; &#39;q&#39;") == "& <tag> 'q'"


def test_breaking_tags_become_newlines_and_collapse() -> None:
    assert to_text("one<br>two") == "one\ntwo"
    assert to_text("<div>one</div><div>two</div>") == "one\ntwo"
    assert to_text("<div>one</div>\n\n  <div>two</div>") == "one\ntwo"


def test_media_becomes_a_readable_placeholder() -> None:
    assert to_text('<img src="diagram.png">') == "[image: diagram.png]"
    assert to_text("hola[sound:hola.mp3]") == "hola [audio]"


def test_an_image_carries_its_filename() -> None:
    """On a vocabulary card the filename is often the whole meaning, and a bare
    [image] throws it away for nothing."""
    assert to_text('<img src="wave.png" class="x">') == "[image: wave.png]"
    assert to_text('<img src="media/sub/el-perro.jpg">') == "[image: el-perro.jpg]"


def test_an_image_with_no_usable_name_is_still_just_an_image() -> None:
    """A data: URI is the case that matters — inlining megabytes of base64 into
    a "plain text" rendering would be worse than saying nothing."""
    assert to_text('<img src="data:image/png;base64,iVBORw0KGgoAAAA">') == "[image]"
    assert to_text('<img src="https://example.com/wave.png">') == "[image]"
    assert to_text("<img>") == "[image]"
    assert to_text(f'<img src="{"a" * 200}.png">') == "[image]"


def test_alt_text_describes_an_image_better_than_its_filename() -> None:
    """`alt` is the author saying in words what the picture is, which is what a
    plain-text rendering exists to carry. Held to the filename's rules: one
    line, no `]`, and short enough not to eat the snippet."""
    assert to_text('<img src="paste-1a2b.jpg" alt="a wave">') == "[image: a wave]"
    assert to_text('<img src="data:image/png;base64,AAAA" alt="the dog">') == "[image: the dog]"
    assert to_text('<img src="x.png" alt="  two\n lines ">') == "[image: two lines]"
    # Unusable alt text falls back to the filename rather than to nothing.
    assert to_text('<img src="wave.png" alt="">') == "[image: wave.png]"
    assert to_text('<img src="wave.png" alt="a]b">') == "[image: wave.png]"
    assert to_text(f'<img src="wave.png" alt="{"a" * 200}">') == "[image: wave.png]"


def test_table_cells_and_rules_do_not_run_together() -> None:
    """A table is one of the few ways a card holds several values side by side,
    and without these as breaks `<td>perro</td><td>dog</td>` became `perrodog`."""
    table = "<table><tr><th>es</th><th>en</th></tr><tr><td>perro</td><td>dog</td></tr></table>"
    assert to_text(table) == "es\nen\nperro\ndog"
    assert to_text("above<hr>below") == "above\nbelow"
    assert snippet(table, 120) == "es en perro dog"


def test_style_and_script_contents_never_reach_the_text() -> None:
    """The same defect this module exists for, one layer down: html.parser puts
    these in CDATA mode and hands the raw CSS to handle_data, so a field with
    pasted styled content rendered as "plain text" that was mostly stylesheet.
    """
    assert to_text("<style>.card { color: red; font-size: 40px; }</style>Hola") == "Hola"
    assert to_text("<script>var x = 1 < 2;</script>Hola") == "Hola"
    assert to_text("<style/>Hola") == "Hola", "a self-closing tag fires no end tag"
    assert to_text("<div>uno</div><style>p{}</style><div>dos</div>") == "uno\ndos"


def test_cloze_markers_survive_verbatim() -> None:
    """They are the entire meaning of a cloze note."""
    assert to_text("<div>The capital is {{c1::Paris}}.</div>") == "The capital is {{c1::Paris}}."


def test_the_real_shape_anki_emits() -> None:
    """One field carrying every construct at once, as measured against a live
    collection: div wrapper, break, nbsp, sound tag, image, cloze."""
    field = (
        '<div style="text-align: left;">Hola<br>&nbsp;amigo</div>'
        "[sound:hola.mp3]"
        '<img src="wave.png">'
        "<div>{{c1::saludo}}</div>"
    )
    assert to_text(field) == "Hola\namigo\n[audio] [image: wave.png]\n{{c1::saludo}}"


# --- the cap counts visible characters, not markup -------------------------


def test_truncate_marks_what_it_dropped() -> None:
    assert truncate("abc", 10) == "abc"
    assert truncate("abcdefghij", 4) == "abcd" + TRUNCATION_SUFFIX


def test_a_snippet_of_heavy_markup_is_mostly_content() -> None:
    """The defect this module exists to fix. A 40-character window over a field
    wrapped in a long style attribute must not return 40 characters of CSS."""
    field = (
        '<div style="text-align: left; font-family: Arial, sans-serif; '
        'font-size: 20px; color: rgb(30,30,30);">The mitochondrion is the '
        "powerhouse of the cell.</div>"
    )
    out = snippet(field, 40)
    assert out.startswith("The mitochondrion")
    assert "style" not in out
    assert "<" not in out


def test_a_snippet_is_one_line() -> None:
    assert "\n" not in snippet("<div>one</div><div>two</div>", 120)


def test_a_snippet_shorter_than_the_cap_is_unmarked() -> None:
    assert snippet("<div>short</div>", 120) == "short"


def test_a_self_closing_tag_is_handled_like_the_open_tag_it_stands_for() -> None:
    """XHTML-style tags are ordinary in Anki fields — the editor writes `<br>`,
    but pasted and add-on-generated HTML is full of `<br/>` and `<img ... />`.
    They arrive through a different parser callback, so a rule applied only in
    `handle_starttag` would silently not apply to half the real input.
    """
    assert to_text("one<br/>two") == "one\ntwo"
    assert "[image: dog.jpg]" in to_text('before<img src="dog.jpg"/>after')


def test_a_self_closing_style_tag_does_not_blank_the_rest_of_the_field() -> None:
    """The reason `handle_startendtag` is not simply an alias. `<style/>` opens
    and closes at once and fires no end tag, so routing it through
    `handle_starttag` would set the opaque flag with nothing left to clear it,
    and every word after it would vanish from the note.
    """
    assert to_text("<style/>The mitochondrion") == "The mitochondrion"
    assert to_text("<script/>still here") == "still here"


# --- cloze markers, for the refusal anki_add_note has to explain -------------
#
# `anki_add_note` uses these to say which way round a cloze mismatch is, so what
# matters is not what ought to count as a deletion but what Anki counts as one.
#
# Every string below was measured on 2026-08-14 by offering it as the `Text` of
# a real Cloze note and recording whether `canAddNotesWithErrorDetail` accepted
# it. Nineteen variants were swept and `has_cloze_deletion` now agrees with the
# add-on on all of them. It did not at first — see the missing-brace case.


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("the ordinary one", "el {{c1::perro}} is the dog"),
        ("numbering need not start at 1", "el {{c2::perro}}"),
        ("two digits", "{{c12::x}}"),
        ("a leading zero", "el {{c01::perro}}"),
        ("a hint after two more colons", "{{c1::perro::the animal}}"),
        ("an empty deletion is still one", "{{c1::}}"),
        ("markup inside the deletion", "{{c1::<b>perro</b>}}"),
        ("inside markup", "<div>el {{c1::perro}}</div>"),
        ("two of them", "el {{c1::a}} y {{c2::b}}"),
        ("spanning newlines", "line one\nel {{c1::perro}}\nline three"),
        ("a stray extra brace after", "el {{c1::perro}}}"),
    ],
)
def test_a_real_cloze_deletion_is_recognised(label: str, text: str) -> None:
    assert has_cloze_deletion(text) is True, label


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("one colon, so inert text", "el {{c1:perro}}"),
        ("no number", "el {{c::perro}}"),
        ("upper-case C", "el {{C1::perro}}"),
        ("a space before the c", "el {{ c1::perro }}"),
        ("single braces", "el {c1::perro}"),
        ("a bare handlebars field", "{{Front}}"),
        ("nothing marker-ish at all", "el perro is the dog"),
    ],
)
def test_something_that_is_not_a_deletion_is_not_counted_as_one(label: str, text: str) -> None:
    """`{{c1:perro}}` is the expensive one: measured, Anki treats it as ordinary
    text, so a Basic note carrying it is accepted and a Cloze note carrying it
    is refused with a message that says only 'unknown reason'."""
    assert has_cloze_deletion(text) is False, label


def test_a_deletion_missing_its_closing_braces_is_not_a_deletion() -> None:
    """The one the sweep caught, and the reason the pattern looks for `}}`.

    Anki refuses `{{c1::perro}` — one closing brace — and a pattern that stopped
    at the opening marker called it a deletion. `anki_add_note` would then have
    told the reader that the Cloze note type does not build cards from cloze
    deletions, which is both confident and nonsense.
    """
    assert has_cloze_deletion("el {{c1::perro}") is False
    assert has_cloze_attempt("el {{c1::perro}") is True, (
        "it must still be recognised as an attempt, or the reader is told a deletion "
        "is missing when what is missing is a brace"
    )


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("one colon", "el {{c1:perro}}"),
        ("upper-case C", "el {{C1::perro}}"),
        ("a space before the c", "el {{ c1::perro }}"),
        ("no number", "{{c::perro}}"),
    ],
)
def test_a_near_miss_is_recognised_as_an_attempt(label: str, text: str) -> None:
    """What separates "you forgot the deletion" from "you wrote one and the
    syntax is wrong". The two need opposite corrections, and the second is the
    one that looks like nothing is wrong."""
    assert has_cloze_attempt(text) is True, label


def test_ordinary_text_is_not_a_cloze_attempt() -> None:
    """`{{Front}}` is a template reference, not a fumbled deletion; a message
    about cloze syntax would send the reader somewhere unhelpful."""
    assert has_cloze_attempt("el perro is the dog") is False
    assert has_cloze_attempt("{{Front}}") is False
    assert has_cloze_attempt("el {c1::perro}") is False
