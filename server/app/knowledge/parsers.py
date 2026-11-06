"""Parsers behind one interface, so a better one can be added without touching ingestion.

`choose_parser` picks one from the profile and the file. Docling is the parser for Arabic pages. If it is not installed,
an Arabic page fails with a clear message: it is never read by the fast parser.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from . import layout
from .layout import Hidden, Options, Raw, parse_pages


class ParserUnavailable(Exception):
    """The chosen parser is not installed, or cannot read this file."""


@dataclass
class Source:
    """What a parser is given: the stored bytes, plus what is known about the file."""

    data: bytes
    filename: str
    content_type: str
    language: str  # the document's language


class Parser(Protocol):
    name: str

    def accepts(self, source: Source) -> bool: ...

    def parse(self, source: Source, opts: Options) -> Raw: ...


def _is_pdf(s: Source) -> bool:
    return s.content_type == "application/pdf" or s.filename.lower().endswith(".pdf") or s.data[:5] == b"%PDF-"


def _is_html(s: Source) -> bool:
    return s.content_type == "text/html" or s.filename.lower().endswith((".html", ".htm"))


class FastPdfParser:
    """PyMuPDF. Quick, and good for Latin text. Optional extra: fast-pdf."""

    name = "fast_text"

    def __init__(self, allow_arabic: bool = False):
        self.allow_arabic = allow_arabic

    def accepts(self, s: Source) -> bool:
        return _is_pdf(s)

    def parse(self, s: Source, opts: Options) -> Raw:
        try:
            import pymupdf  # noqa: F401
        except ImportError as e:
            raise ParserUnavailable("The fast PDF parser is not installed. Install the fast-pdf extra.") from e
        if (s.language == "ar" or "ar" in opts.ocr_languages) and not self.allow_arabic:
            raise layout.ArabicNotSupported(1)
        opts.allow_arabic = self.allow_arabic
        return layout.parse_pdf_pymupdf(s.data, opts)


class DoclingParser:
    """Layout models, and the parser for Arabic. Optional extra: docling."""

    name = "best_layout"

    def accepts(self, s: Source) -> bool:
        return _is_pdf(s)

    def parse(self, s: Source, opts: Options) -> Raw:
        if not layout.docling_available():
            raise ParserUnavailable("Best layout needs Docling, which is not installed. Install the docling extra.")
        return layout.parse_pdf_docling(s.data, opts)


class HtmlParser:
    name = "web_page"

    def accepts(self, s: Source) -> bool:
        return _is_html(s)

    def parse(self, s: Source, opts: Options) -> Raw:
        return layout.parse_html(s.data, opts)


class TextParser:
    """Plain text, Markdown and CSV tables."""

    name = "table"

    def accepts(self, s: Source) -> bool:
        return not _is_pdf(s) and not _is_html(s)

    def parse(self, s: Source, opts: Options) -> Raw:
        return layout.parse_text(s.data, s.filename, opts)


class StoredTextParser:
    """For a version that has chunks but no stored file, such as one loaded from the demo data. It reads the chunks it already has."""

    name = "stored"

    def __init__(self, chunks: list[dict]):
        self.chunks = chunks

    def accepts(self, s: Source) -> bool:
        return not s.data

    def parse(self, s: Source, opts: Options) -> Raw:
        blocks = [layout._block("paragraph", c["text"], c.get("page"), article=c.get("article") or "") for c in self.chunks]
        for b, c in zip(blocks, self.chunks):
            b["page_end"] = c.get("page_end") or c.get("page")
        pages = [c["page"] for c in self.chunks if c.get("page")]
        return Raw(blocks, [], max(pages, default=1))


def choose_parser(profile_parser: str, source: Source, stored: Optional[list[dict]] = None, arabic_text_layer: bool = False) -> Parser:
    """The file decides what can read it; the profile decides which reader for a PDF."""
    if not source.data:
        if stored:
            return StoredTextParser(stored)
        raise ParserUnavailable("No file is stored for this version. Upload the file first.")
    if _is_html(source):
        return HtmlParser()
    if not _is_pdf(source):
        return TextParser()
    if arabic_text_layer and not layout.docling_available():
        return FastPdfParser(allow_arabic=True)
    return FastPdfParser() if profile_parser == "fast_text" else DoclingParser()


def apply_profile_pages(raw: Raw, spec: Optional[str]) -> Raw:
    """Keeps the pages named in a page range. A parser that takes the range itself leaves nothing to drop."""
    pages = parse_pages(spec, raw.page_count)
    if pages is None:
        return raw
    raw.blocks = [b for b in raw.blocks if b["page"] is None or b["page"] in pages]
    raw.hidden = [h for h in raw.hidden if h.page is None or h.page in pages]
    raw.image_only = [p for p in raw.image_only if p in pages]
    return raw


def drop_annexes(blocks: list[dict]) -> list[dict]:
    """From the first heading that opens an annex, schedule or appendix to the end."""
    import re

    start = re.compile(r"^\s*(annex(?:ure)?|appendix|schedule|الملحق|ملحق)\b", re.I)
    for i, b in enumerate(blocks):
        if b["type"] == "heading" and start.match(b["text"]):
            return blocks[:i]
    return blocks


__all__ = ["Parser", "ParserUnavailable", "Source", "Hidden", "choose_parser", "FastPdfParser", "DoclingParser"]
