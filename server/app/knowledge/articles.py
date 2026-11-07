"""Article headings in parsed blocks. Pure: blocks in, blocks out, no file and no database.

Parsers often leave an article heading inside a paragraph, either as its first line ("Article 7" then the body) or, in Arabic laws,
run into one line after the previous sentence ("... ( المادة 13 ) title body"). mark_articles turns both into level-3 heading blocks,
so the chunker starts a chunk at each article and labels it.
"""
from __future__ import annotations

import re

from .layout import ARABIC_RE, DIGITS_AR, Raw, legal_level

# The heading form brackets the word: ( المادة 13 ). A reference brackets only the number: المادة (13), and is not split.
INLINE = re.compile(r"[\(\)]\s*(?:المادة|املادة|مادة)\s*([\d٠-٩]{1,3})\s*[\(\)]")
_TEXT_TYPES = ("paragraph", "list_item", "heading")


def split_inline(blocks: list[dict]) -> list[dict]:
    """Splits Arabic text blocks at each ( المادة N ) mark into a level-3 heading and the body that follows it."""
    out: list[dict] = []
    for b in blocks:
        t = b.get("text") or ""
        marks = list(INLINE.finditer(t)) if b.get("type") in _TEXT_TYPES and ARABIC_RE.search(t) else []
        if not marks:
            out.append(b)
            continue
        before = t[:marks[0].start()].strip()
        if before:
            out.append({**b, "text": before})
        for i, m in enumerate(marks):
            out.append({**b, "type": "heading", "level": 3, "text": f"المادة ({m.group(1).translate(DIGITS_AR)})"})
            body = t[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(t)].strip()
            if body:
                out.append({**b, "type": "paragraph", "text": body})
    return out


def promote(blocks: list[dict]) -> list[dict]:
    """A paragraph or list item whose first line is an article heading becomes a level-3 heading, and the rest a paragraph."""
    out: list[dict] = []
    for b in blocks:
        if b.get("type") in ("paragraph", "list_item") and b.get("text"):
            first, _, rest = b["text"].strip().partition("\n")
            if legal_level(first)[0] == 3:
                out.append({**b, "type": "heading", "level": 3, "text": first.strip()})
                if rest.strip():
                    out.append({**b, "type": "paragraph", "text": rest.strip()})
                continue
        out.append(b)
    return out


def mark_articles(raw: Raw) -> Raw:
    """Marks the article headings of a parsed document, in place. Run after the Arabic cleanup, so the headings are read as clean text."""
    raw.blocks = promote(split_inline(raw.blocks))
    return raw
