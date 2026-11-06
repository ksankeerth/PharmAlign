"""Repairs Arabic as PDF text layers give it, so the normal chunker and embedder can use it. Pure, and it reads no file.

Text is stored in reading order. Direction is only how a screen draws it, so nothing here turns text around unless a line is proven to
be backwards. The faults it repairs:

- letters held as display shapes (U+FB50 and up), which the embedder reads as other characters
- lines stored backwards, seen from the shapes: a word that opens with a final form was stored from its end
- kashida stretching, and digits fused to letters
- swapped lam-alef and lam-meem pairs. These repairs are guesses from word shape, so they run only on a document the quality gate
  flags, and never touch a word such as كاملة, which only looks swapped
"""
from __future__ import annotations

import re
import unicodedata

from .layout import Raw

LETTERS = "ء-ي"
ARABIC = re.compile(f"[{LETTERS}ﭐ-﷿ﹰ-ﻼ]")
LATIN = re.compile(r"[A-Za-z]")
SHAPES = re.compile("[ﭐ-﷿ﹰ-ﻼ]")
WORD = re.compile(f"[{LETTERS}ﭐ-﷿ﹰ-ﻼ]+")
UNTURNED = re.compile(r"[0-9A-Za-z][0-9A-Za-z.,/%-]*")
MIRROR = str.maketrans("()[]{}<>", ")(][}{><")
KASHIDA = "ـ"
FUSED = re.compile(f"(?<=[{LETTERS}])(?=[0-9])|(?<=[0-9])(?=[{LETTERS}])")
ARABIC_TOKEN = re.compile(f"[{LETTERS}]+")
SWAPPED_START = [
    (re.compile("^([وفب]?)امل(?=[" + LETTERS + "]{2})"), r"\1الم"),
    (re.compile("^([وفب]?)اإل"), r"\1الإ"),
    (re.compile("^([وفب]?)األ"), r"\1الأ"),
    (re.compile("^([وفب]?)اال"), r"\1الا"),
    (re.compile("^لإل"), "للإ"),
    (re.compile("^لأل"), "للأ"),
    (re.compile("^لال"), "للا"),
]
SWAPPED_WORDS = {"خالل": "خلال", "عالج": "علاج", "سالمة": "سلامة", "صالحية": "صلاحية", "إعالن": "إعلان", "إبالغ": "إبلاغ", "إغالق": "إغلاق"}


def is_arabic(text: str) -> bool:
    """True when most of the letters are Arabic."""
    return len(ARABIC.findall(text)) > len(LATIN.findall(text))


def _form(char: str) -> str:
    return unicodedata.decomposition(char).split(" ")[0]


def _is_backwards(line: str) -> bool:
    backwards = forwards = 0
    for word in WORD.findall(line):
        if len(word) < 2:
            continue
        first, last = _form(word[0]), _form(word[-1])
        backwards += first == "<final>" or last == "<initial>"
        forwards += first == "<initial>" or last == "<final>"
    return backwards > forwards


def _turn(line: str) -> str:
    """Reverses a backwards line, and puts numbers and Latin words back as they were."""
    turned = line[::-1].translate(MIRROR)
    return UNTURNED.sub(lambda m: m.group(0)[::-1], turned)


def clean_line(line: str) -> str:
    if not ARABIC.search(line):
        return line
    if _is_backwards(line):
        line = _turn(line)
    line = SHAPES.sub(lambda m: unicodedata.normalize("NFKC", m.group(0)), line).replace(KASHIDA, "")
    return FUSED.sub(" ", line)


def clean_text(text: str) -> str:
    return "\n".join(clean_line(line) for line in text.split("\n"))


def _repair_word(word: str) -> str:
    if word == "ال":
        return "لا"
    for pattern, repaired in SWAPPED_START:
        word = pattern.sub(repaired, word, count=1)
    for wrong, right in SWAPPED_WORDS.items():
        if wrong in word:
            word = word.replace(wrong, right)
    return word.replace("يالت", "يلات").replace("اال", "الا")


def repair_swapped(text: str) -> str:
    return ARABIC_TOKEN.sub(lambda m: _repair_word(m.group(0)), text)


def _map_blocks(raw: Raw, change) -> None:
    for block in raw.blocks:
        if block.get("text"):
            block["text"] = change(block["text"])
        if block.get("rows"):
            block["rows"] = [[change(cell) if isinstance(cell, str) else cell for cell in row] for row in block["rows"]]


def clean_raw(raw: Raw) -> Raw:
    """Cleans a parsed document in place. Swap repairs run only when the quality gate would fail its Arabic."""
    from .quality import LIMITS, arabic_stats

    _map_blocks(raw, clean_text)
    body = "\n".join(b.get("text") or " ".join(str(c) for r in (b.get("rows") or []) for c in r) for b in raw.blocks)
    stats = arabic_stats(body)
    if stats and (stats.lam_alef_per_1000 > LIMITS.lam_alef_per_1000 or stats.lam_meem_per_1000 > LIMITS.lam_meem_per_1000):
        _map_blocks(raw, repair_swapped)
    return raw
