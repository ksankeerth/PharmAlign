"""The quality gate, run on the text the chosen parser actually produced.

A script-range check only sees "this is Arabic", so swapped letters pass it. The gate counts letter errors:
odd-script letters inside Arabic text, the broken lam-alef (`اال`) and the swapped lam-meem (`امل`) prefixes.

Hidden text is scanned too, with care about false holds. Hidden text with an instruction in it holds the file for a
person. Hidden text alone (a watermark, a banner) is flagged, taken out of what is read, and the file goes on.
An instruction in visible text is policy text like any other: it is stored as data and never followed.

Nothing a document says is ever an instruction. Documents are stored and searched as text.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional, Sequence

from pharmalign_contracts.server import models as m

from .layout import ARABIC_RE, Hidden, Raw, legal_level


@dataclass(frozen=True)
class GateLimits:
    """Where the gate turns from pass to fail. Measured on a set of real Arabic and English files."""

    min_arabic_letters: int = 200  # fewer than this and there is too little text to judge
    odd_script_per_1000: float = 5.0  # Latin-extended, Cyrillic and similar letters among Arabic letters
    lam_alef_per_1000: float = 3.0  # tokens with اال per 1000 Arabic tokens
    lam_meem_per_1000: float = 3.0  # tokens starting امل per 1000 Arabic tokens
    bad_chars_per_1000: float = 5.0  # replacement and control characters, any language
    lost_words_share: float = 0.05
    near_duplicate: float = 0.8


LIMITS = GateLimits()

# Latin Extended, IPA, Cyrillic, Canadian syllabics, Arabic Extended-B/Supplement: not letters of Arabic text.
ODD_SCRIPT = re.compile("[Ā-ʯЀ-ӿ᐀-ᙿࡰ-ࣿݐ-ݿ]")
ARABIC_TOKEN = re.compile("[ء-يٱ-ۓ]+")
BAD_CHARS = re.compile("[�\x00-\x08\x0b\x0c\x0e-\x1f-]")


@dataclass
class ArabicStats:
    letters: int
    odd_script_per_1000: float
    lam_alef_per_1000: float
    lam_meem_per_1000: float


def arabic_stats(text: str) -> Optional[ArabicStats]:
    """None when there is too little Arabic to judge."""
    t = unicodedata.normalize("NFKC", text)
    letters = len(ARABIC_RE.findall(t))
    if letters < LIMITS.min_arabic_letters:
        return None
    tokens = ARABIC_TOKEN.findall(t)
    n = max(1, len(tokens))
    return ArabicStats(
        letters=letters,
        odd_script_per_1000=round(1000 * len(ODD_SCRIPT.findall(t)) / letters, 1),
        lam_alef_per_1000=round(1000 * sum(1 for x in tokens if "اال" in x or "اا" + "ل" in x) / n, 1),
        lam_meem_per_1000=round(1000 * sum(1 for x in tokens if x.startswith("امل") or x.startswith("وامل")) / n, 1),
    )


def garble_verdict(text: str) -> tuple[str, str, Optional[ArabicStats]]:
    """(status, detail, arabic stats) for the garbled_text check."""
    n = max(1, len(text))
    bad = round(1000 * len(BAD_CHARS.findall(text)) / n, 1)
    stats = arabic_stats(text)
    problems = []
    if stats:
        if stats.odd_script_per_1000 > LIMITS.odd_script_per_1000:
            problems.append(f"{stats.odd_script_per_1000} odd-script letters per 1000 Arabic letters (limit {LIMITS.odd_script_per_1000:g})")
        if stats.lam_alef_per_1000 > LIMITS.lam_alef_per_1000:
            problems.append(f"{stats.lam_alef_per_1000} broken lam-alef words per 1000 (limit {LIMITS.lam_alef_per_1000:g})")
        if stats.lam_meem_per_1000 > LIMITS.lam_meem_per_1000:
            problems.append(f"{stats.lam_meem_per_1000} swapped lam-meem words per 1000 (limit {LIMITS.lam_meem_per_1000:g})")
    if bad > LIMITS.bad_chars_per_1000:
        problems.append(f"{bad} unreadable characters per 1000 (limit {LIMITS.bad_chars_per_1000:g})")
    if problems:
        return "fail", "Letters are wrong in the text layer: " + "; ".join(problems) + ".", stats
    return "pass", ("Arabic letters read correctly." if stats else None), stats


# ------------------------------------------------------------------ instructions and hidden text

PHRASES = {
    "ignore_previous": r"(?:ignore|disregard|forget|override)\s*(?:all|any|the|your|every)?\s*(?:previous|prior|above|earlier|preceding|system)\s*(?:instructions?|rules?|prompts?|guidelines?|context)",
    "mark_as_met": r"(?:mark|set|record|report|rate|treat|classify|label|answer|respond)\b.{0,60}?\b(?:as|with|that)\s*(?:all\s*)?(?:met|compliant|complete|approved|passed?|satisfied|conforming)\b",
    "you_are": r"\byou\s*are\s*(?:now\s*)?(?:an?\s*)?(?:ai|assistant|language\s*model|reviewer|helpful|compliance)",
    "system_prompt": r"system\s*prompt|developer\s*message|hidden\s*instruction",
    "do_not_flag": r"do\s*not\s*(?:flag|report|mention|disclose|raise|reveal|escalate)\b",
    "always_approve": r"(?:always|must|should)\s*(?:approve|pass|answer\s*(?:yes|met)|mark\s*(?:as\s*)?met)",
    "new_instructions": r"new\s*instructions?\s*:|updated\s*instructions?\s*:|important\s*:\s*(?:ignore|disregard)",
    "role_tags": r"</?\s*(?:system|instructions?|assistant)\s*>|\[\s*(?:system|inst)\s*\]|\bassistant\s*:|<<\s*sys\s*>>",
    "ar_ignore": "تجاهل\\s*(?:جميع|كل)?\\s*(?:ال)?(?:تعليمات|أوامر|قواعد)|التعليمات\\s*السابقة|تعليمات\\s*النظام",
    "ar_mark_met": "(?:اعتبر|ضع\\s*علامة|سجل|صنف)\\s*(?:جميع|كل)?.{0,40}?(?:مستوفى|مستوفاة|ممتثل|مقبول|موافق|مطابق)",
    "ar_do_not_flag": "لا\\s*(?:تبلغ|تذكر|تشر|تكشف)",
}
PHRASE_RX = {k: re.compile(v, re.I | re.S) for k, v in PHRASES.items()}
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍‎‏‪‫‬‭‮⁦⁧⁨⁩﻿­⁠"), None)
_LOOKALIKE = str.maketrans("оаерсіѕухԁ", "oaepcisyxd")  # Cyrillic letters that look Latin
_TASHKEEL = re.compile("[ً-ٰ؟ـ]")


def instruction_hits(text: str) -> list[str]:
    """Names of the instruction-like phrases in a text. Spacing tricks and look-alike letters do not hide them."""
    t = unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH).translate(_LOOKALIKE)
    t = _TASHKEEL.sub("", t)
    squeezed = re.sub(r"\s+", "", t)
    return sorted(k for k, rx in PHRASE_RX.items() if rx.search(t) or rx.search(squeezed))


@dataclass
class HiddenVerdict:
    status: str  # pass, attention
    detail: Optional[str]
    held: bool
    flagged: list[m.FlaggedPage] = field(default_factory=list)


def hidden_verdict(hidden: Sequence[Hidden], meta_text: str = "") -> HiddenVerdict:
    """Hold only when something hidden carries an instruction. Hidden text alone is flagged and removed, not held."""
    in_meta = instruction_hits(meta_text) if meta_text else []
    if not hidden and not in_meta:
        return HiddenVerdict("pass", None, False)
    joined = " ".join(h.text for h in hidden)
    phrases = sorted(set(instruction_hits(joined) + in_meta))
    by_reason: dict[str, int] = {}
    for h in hidden:
        by_reason[h.reason] = by_reason.get(h.reason, 0) + 1
    pages = sorted({h.page or 1 for h in hidden})  # a page-less format such as HTML counts as page 1
    flagged = [m.FlaggedPage(page=p, reason="hidden_text", detail=f"{sum(1 for h in hidden if (h.page or 1) == p)} hidden text span(s) removed.") for p in pages[:50]]
    kinds = ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in sorted(by_reason.items()))
    if phrases:
        where = "in the file's metadata" if in_meta and not hidden else f"in hidden text ({kinds})"
        return HiddenVerdict("attention", f"Held for a person. An instruction-like phrase ({', '.join(phrases)}) was found {where}. Nothing in it is followed.", True, flagged)
    return HiddenVerdict("attention", f"{kinds} of hidden text were found and removed from what is read. No instruction in it. A person can look.", False, flagged)


# ------------------------------------------------------------------ near duplicates

SKETCH_SIZE = 128
SHINGLE = 10


def sketch_of(text: str) -> list[int]:
    """The smallest 128 hashes of the text's 10-word shingles. Two sketches estimate how much text two documents share."""
    ws = re.findall(r"\w+", unicodedata.normalize("NFKC", text).lower())
    if len(ws) < SHINGLE:
        return []
    hashes = {int.from_bytes(hashlib.blake2b(" ".join(ws[i:i + SHINGLE]).encode(), digest_size=8).digest(), "big") >> 1 for i in range(len(ws) - SHINGLE + 1)}
    return sorted(hashes)[:SKETCH_SIZE]


def shared_text(a: Sequence[int], b: Sequence[int]) -> float:
    """Estimated Jaccard similarity of two sketches."""
    if not a or not b:
        return 0.0
    union = sorted(set(a) | set(b))[:SKETCH_SIZE]
    sa, sb = set(a), set(b)
    return sum(1 for h in union if h in sa and h in sb) / len(union)


# ------------------------------------------------------------------ the report

@dataclass
class Assessment:
    report: m.QualityReport
    held: bool
    sketch: list[int]
    duplicate_of: Optional[str]
    garble: Optional[ArabicStats]
    unread_arabic: bool = False


def _worst(statuses: Sequence[str]) -> str:
    return "fail" if "fail" in statuses else "attention" if "attention" in statuses else "pass"


def article_numbers(chunks: Sequence[dict]) -> list[int]:
    out = set()
    for c in chunks:
        for line in (c.get("section_path") or "").split(" > "):
            lvl, art = legal_level(line)
            if lvl == 3 and art.isdigit():
                out.add(int(art))
    return sorted(out)


def assess(
    version_id: str, raw: Raw, chunks: Sequence[dict], *, language: str, doc_type: str, images: str, split_by: str, split_count: int,
    max_chars: int, others: Sequence[tuple[str, Sequence[int]]] = (),
) -> Assessment:
    """Runs every check on the parser's output. `others` are (version id, sketch) pairs of other stored versions."""
    body = "\n".join(b.get("text") or " ".join(" ".join(r) for r in (b.get("rows") or [])) for b in raw.blocks)
    chunk_text = "\n".join(c["text"] for c in chunks)
    checks: list[m.QualityCheck] = []
    flagged: list[m.FlaggedPage] = []

    # Text layer: pages with nothing to read.
    if raw.image_only:
        if images == "ignore_all":
            checks.append(m.QualityCheck(key="text_layer", status="pass", detail=f"{len(raw.image_only)} image-only pages were ignored, as chosen."))
        else:
            checks.append(m.QualityCheck(key="text_layer", status="attention", detail=f"{len(raw.image_only)} pages have no text layer. They are flagged, not read."))
            flagged += [m.FlaggedPage(page=p, reason="image_only", detail="No text layer.") for p in raw.image_only[:50]]
    else:
        checks.append(m.QualityCheck(key="text_layer", status="pass"))

    ar, lat = len(ARABIC_RE.findall(body)), len(re.findall(r"[A-Za-z]", body))
    detected = "ar" if ar > lat else "en" if lat else language
    checks.append(m.QualityCheck(
        key="language_detected", status="pass" if detected == language else "attention",
        detail=f"Detected {detected}." if detected == language else f"Detected {detected}, but the document says {language}."))

    status, detail, stats = garble_verdict(body)
    checks.append(m.QualityCheck(key="garbled_text", status=status, detail=detail))
    if status == "fail":
        flagged.append(m.FlaggedPage(page=1, reason="garbled", detail="Wrong letters in the text layer."))

    expected = found = None
    if split_by == "article" or doc_type in ("law", "executive_regulation"):
        nums = article_numbers(chunks)
        if nums:
            expected, found = max(nums), len(nums)
            missing = expected - found
            checks.append(m.QualityCheck(
                key="articles_complete", status="pass" if not missing else "attention",
                detail="Every expected article was found." if not missing else f"{missing} article numbers between 1 and {expected} were not found."))
    checks.append(m.QualityCheck(
        key="chunk_size_cap", status="pass" if not split_count else "attention",
        detail=f"No chunk exceeds {max_chars} characters." if not split_count else f"{split_count} long chunks were split to fit {max_chars} characters."))

    raw_words = len(body.split())
    kept_words = len(chunk_text.split())
    lost = max(0, raw_words - kept_words)
    checks.append(m.QualityCheck(
        key="words_lost", status="attention" if raw_words and lost / raw_words > LIMITS.lost_words_share else "pass",
        detail=f"About {lost} words are not in any chunk." if lost else "No words lost."))

    verdict = hidden_verdict(raw.hidden, raw.meta_text)
    checks.append(m.QualityCheck(key="hidden_text", status=verdict.status, detail=verdict.detail))
    flagged += verdict.flagged

    sketch = sketch_of(chunk_text)
    dup, share = None, 0.0
    for other_id, other in others:
        s = shared_text(sketch, other)
        if s > share:
            dup, share = other_id, s
    near = share >= LIMITS.near_duplicate
    checks.append(m.QualityCheck(
        key="duplicate_text", status="attention" if near else "pass",
        detail=f"About {round(share * 100)}% of this text is also in {dup}. Flagged, not merged." if near else None))

    texts = [c["text"] for c in chunks]
    hits = sorted({h for t in texts for h in instruction_hits(t)})
    if hits:
        checks.append(m.QualityCheck(
            key="instruction_like_text", status="pass",
            detail=f"Some passages read like instructions ({', '.join(hits)}). They are stored as text and never followed."))

    report = m.QualityReport(
        document_version_id=version_id, status=_worst([c.status for c in checks]), checks=checks, held=verdict.held, flagged_pages=flagged[:200],
        expected_articles=expected, found_articles=found, words_lost=lost,
    )
    return Assessment(report, verdict.held, sketch, dup if near else None, stats)
