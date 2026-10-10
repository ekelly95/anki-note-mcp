"""Anki note fields are HTML. This turns them into something worth reading.

The source spec caps snippets with a plain character slice, which is a real
defect at the one tool designed to be cheap: a 120-character window over
`<div style="text-align: left;">` carries no information at all, so the search
tool spends context and returns nothing usable. Progressive disclosure only
works if the cheap call is genuinely cheap in information terms, not just in
bytes.

Stdlib only — `html.parser` plus `re`. No new dependency for this.

What survives normalization, and why:
  - cloze markers `{{c1::...}}` are plain text in the field and are preserved
    verbatim; they are the whole meaning of a cloze note.
  - `[sound:x.mp3]` is literal text, not a tag, and becomes `[audio]` — no
    filename, deliberately. An `<img>` replaces content that has no other
    representation, so its filename recovers something otherwise lost; audio on
    a card almost always pronounces text that is already in the field, so its
    filename would spend snippet budget repeating what is there.
  - `<img>` becomes `[image: wave.png]` — for a vocabulary card the filename is
    often the meaning, and it costs a few characters to keep it. Its `alt` text
    wins when it has one: that is the author describing the picture in words,
    which is what a plain-text rendering is for.
  - table cells and `<hr>` break lines like the block tags do, so adjacent
    cells do not run together into one word.
  - `<style>` and `<script>` contents are dropped. `html.parser` puts them in
    CDATA mode and hands the raw CSS or JS to `handle_data`, so a field with
    pasted styled content otherwise renders as "plain text" that is mostly
    stylesheet — the same defect this module exists to fix, one layer down.
  - `&nbsp;` unescapes to U+00A0, which reads as a space but is not one; it is
    folded to a real space so downstream length caps and matching behave.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# Tags whose boundaries are a line break in the rendered card.
_BREAKING = frozenset(
    {
        "br",
        "div",
        "p",
        "li",
        "tr",
        "td",
        "th",
        "hr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
    }
)

# Tags whose text is markup for the renderer, not content for a reader.
_OPAQUE = frozenset({"style", "script"})

# Anki stores media as a bare filename in collection.media, so anything with a
# scheme came from somewhere else — and a data: URI can be megabytes of base64,
# which is the one thing a plain-text rendering must never inline.
_HAS_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_MAX_IMAGE_NAME = 48

_SOUND_RE = re.compile(r"\[sound:[^\]]*\]")
# \xa0 is the non-breaking space that &nbsp; unescapes to. Written as an
# escape rather than the literal character, which is invisible in a source file.
_HORIZONTAL_WS_RE = re.compile(r"[ \t\xa0]+")
_AROUND_NEWLINE_RE = re.compile(r"\s*\n\s*")

TRUNCATION_SUFFIX = "…[truncated]"

# Matched against raw field HTML rather than rendered text, because that is what
# Anki reads when it decides whether a cloze note generates any cards.
#
# The numbering does not have to start at 1: measured 2026-08-14, a note whose
# only deletion is `{{c2::...}}` is accepted, as is `{{c01::...}}`. So `\d+` and
# not a literal 1.
#
# The closing `}}` is load-bearing and was missing at first. `{{c1::perro}` with
# one brace is refused by Anki, and a pattern that stopped at the opening marker
# called it a deletion — which sent `_explain_cloze_mismatch` down the branch
# that says the note type does not do cloze, about the Cloze note type. A
# message that confident and that wrong is worse than the one it replaced.
#
# `[\s\S]` rather than `.` because a field is HTML and may carry newlines, and
# non-greedy so `{{c1::a}} {{c2::b}}` is two deletions rather than one long one.
_CLOZE_RE = re.compile(r"\{\{c\d+::[\s\S]*?\}\}")
# Anything that was reaching for the syntax and missed. Case-insensitive because
# `{{C1::x}}` is a plausible thing to type and not a deletion — measured the
# same day, `{{c1:x}}` with one colon is inert text on a Basic note and refuses a
# Cloze one, which is precisely the mistake that looks like nothing is wrong.
_CLOZE_ATTEMPT_RE = re.compile(r"\{\{\s*c", re.IGNORECASE)


def _usable_label(text: str) -> bool:
    # A `]` would close the marker early and a long label would eat the whole
    # snippet budget the caller came for.
    return bool(text) and "]" not in text and len(text) <= _MAX_IMAGE_NAME


def _image_label(attrs: list[tuple[str, str | None]]) -> str:
    """`[image]`, carrying the alt text or the media filename when usable."""
    alt = " ".join((next((value for name, value in attrs if name == "alt"), None) or "").split())
    if _usable_label(alt):
        return f" [image: {alt}] "

    src = next((value for name, value in attrs if name == "src"), None) or ""
    if not src or _HAS_SCHEME_RE.match(src):
        return " [image] "

    name = src.replace("\\", "/").rsplit("/", 1)[-1]
    if not _usable_label(name):
        return " [image] "
    return f" [image: {name}] "


class _TextExtractor(HTMLParser):
    """Collects visible text, mapping structural tags to whitespace."""

    def __init__(self) -> None:
        # convert_charrefs=True resolves &amp;/&nbsp;/&#39; inside handle_data.
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._opaque = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _OPAQUE:
            # A bool rather than a depth count: the parser switches to CDATA
            # mode here, so a nested <style> is data, not a tag, and can never
            # open a second level. An unclosed one swallows the rest of the
            # field, which is the safe direction to fail in.
            self._opaque = True
        elif tag == "img":
            self.parts.append(_image_label(attrs))
        elif tag in _BREAKING:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # `<style/>` opens and closes at once and fires no end tag, so routing
        # it through handle_starttag would set a flag nothing ever clears.
        if tag in _OPAQUE:
            return
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in _OPAQUE:
            self._opaque = False
        elif tag in _BREAKING:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._opaque:
            self.parts.append(data)


def to_text(field_html: str) -> str:
    """Render one Anki field's HTML as plain text."""
    parser = _TextExtractor()
    parser.feed(field_html)
    parser.close()
    text = "".join(parser.parts)

    text = _SOUND_RE.sub(" [audio] ", text)
    text = _HORIZONTAL_WS_RE.sub(" ", text)
    text = _AROUND_NEWLINE_RE.sub("\n", text)
    return text.strip()


def has_cloze_deletion(field_html: str) -> bool:
    """Does this field carry a cloze deletion Anki will actually act on?"""
    return _CLOZE_RE.search(field_html) is not None


def has_cloze_attempt(field_html: str) -> bool:
    """Does this field carry something that was MEANT to be a cloze deletion?

    Only worth asking when `has_cloze_deletion` has already said no. It is what
    separates "you forgot the deletion" from "you wrote one and the syntax is
    wrong", and those need different corrections.
    """
    return _CLOZE_ATTEMPT_RE.search(field_html) is not None


def truncate(text: str, limit: int) -> str:
    """Cap `text` at `limit` characters, marking it when anything was dropped."""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + TRUNCATION_SUFFIX


def snippet(field_html: str, limit: int) -> str:
    """Normalize a field to text and cap it. The count is of visible characters.

    Newlines collapse to spaces here: a snippet is a one-line preview, and a
    multi-line one wastes the caller's attention as much as the markup did.
    """
    return truncate(to_text(field_html).replace("\n", " "), limit)
