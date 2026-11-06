"""The citation check: is each quote really in the chunk its anchor names? Pure, with no database.

Matching is exact after normalising text variants, on word boundaries. It is never fuzzy, since a fuzzy match accepts a
changed number. Guards refuse text that is true but proves nothing: too short, repeated, or copied from elsewhere.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Literal, Mapping, Optional, Sequence

from pharmalign_contracts.common import Evidence, FactValue

MIN_WORDS = 8
LONG_QUOTE_WORDS = 12  # a quote this long may also appear once elsewhere in its document

ARABIC_MARKS = re.compile("[ؐ-ؚـً-ٰٟۖ-ۭ]")
FOLD = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ی": "ي", "ئ": "ي", "ؤ": "و", "ة": "ه", "ک": "ك", "ھ": "ه",
    "٫": ".", "٬": ",", "٪": "%",
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
})
TOKEN = re.compile(r"(?<![\w.,])-?\d+(?:[.,]\d+)*|\d+(?:[.,]\d+)*|%|[^\W\d_]+")
THOUSANDS = re.compile(r"-?\d{1,3}(?:,\d{3})+")
WRAPPED_WORD = re.compile(r"(?<=[^\W\d_])-\s*(?=[^\W\d_])")
UNITS = frozenset({
    "c", "f", "k", "%", "mg", "g", "kg", "mcg", "ug", "μg", "ml", "l", "iu", "month", "months", "year", "years", "week", "weeks",
    "day", "days", "hour", "hours", "minute", "minutes", "ملغ", "مغ", "جم", "غ", "مل", "شهر", "شهرا", "اشهر", "سنه", "سنوات", "يوم", "ايام", "ساعه", "ساعات",
})
FILLER = frozenset({"a", "an", "the", "of", "in", "at", "for", "to", "and", "or", "is", "are", "was", "were", "في", "من", "على", "و"})


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    text = ARABIC_MARKS.sub("", text).translate(FOLD).casefold()
    return "".join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in text)


def _tokens(cleaned: str) -> list[str]:
    return [t.replace(",", "") if THOUSANDS.fullmatch(t) else t for t in TOKEN.findall(cleaned)]


def normalise(text: str) -> str:
    """One spelling for the variants of a text: NFKC, Arabic letters and diacritics, spacing, hyphens, digits."""
    return " ".join(_tokens(_clean(text)))


def _joined(text: str) -> str:
    """The same, with a hyphen inside a word closed up, as in a word wrapped across two lines."""
    return " ".join(_tokens(WRAPPED_WORD.sub("", _clean(text))))


def _holds(needle: str, haystack: str) -> bool:
    return bool(needle) and f" {needle} " in f" {haystack} "


def quote_in(quote: str, text: str) -> bool:
    return _holds(normalise(quote), normalise(text)) or _holds(_joined(quote), _joined(text))


def numbers_in(text: str) -> list[float]:
    found = []
    for token in _tokens(_clean(text)):
        if token[-1].isdigit():
            try:
                found.append(float(token.replace(",", ".")))
            except ValueError:
                pass
    return found


def quote_is_long_enough(quote: str) -> bool:
    """About eight words, or a number with its unit and what it measures."""
    tokens = normalise(quote).split()
    if len(tokens) >= MIN_WORDS:
        return True
    has_measure = any(t[-1].isdigit() and i + 1 < len(tokens) and tokens[i + 1] in UNITS for i, t in enumerate(tokens))
    return has_measure and any(t[-1].isalpha() and t not in UNITS and t not in FILLER for t in tokens)


@dataclass(frozen=True)
class SourceChunk:
    anchor: str
    text: str
    origin: Literal["kb", "dossier", "notice", "reference"]
    document: str  # the document version it sits in
    doc_id: str = ""
    in_force: bool = True
    suspect: bool = False


@dataclass(frozen=True)
class CitationContext:
    """`chunks` holds the cited chunks and the rest of their documents, from the case's pinned snapshot and dossier only."""

    chunks: Mapping[str, SourceChunk]
    question: str = ""
    other_outputs: Sequence[str] = ()  # what other agents wrote about this case
    other_evidence: Sequence[Evidence] = field(default_factory=tuple)


@dataclass(frozen=True)
class CitationFailure:
    anchor: str
    rule: str
    message: str


def check_quote(e: Evidence, ctx: CitationContext) -> Optional[CitationFailure]:
    def fail(rule: str, message: str) -> CitationFailure:
        return CitationFailure(e.anchor, rule, f"{e.anchor}: {message}")

    chunk = ctx.chunks.get(e.anchor)
    if chunk is None:
        return fail("anchor_not_found", "this anchor is not in the case's pinned snapshot, dossier or reference documents.")
    if chunk.suspect:
        return fail("suspect_page", "this page has hidden text, so a quote from it can't stand alone.")
    if not chunk.in_force:
        return fail("not_in_force", "this source is not in force in the pinned snapshot.")
    if not quote_in(e.quote, chunk.text):
        return fail("quote_not_found", "the quote is not in this chunk.")
    if not quote_is_long_enough(e.quote):
        return fail("quote_too_short", "the quote is too short to show where it came from.")
    if quote_in(e.quote, ctx.question):
        return fail("quote_from_question", "the quote only repeats the question.")
    if any(quote_in(e.quote, text) for text in ctx.other_outputs):
        return fail("quote_copied", "the quote repeats another agent's output.")
    if any(o.anchor != e.anchor and normalise(o.quote) == normalise(e.quote) for o in ctx.other_evidence):
        return fail("quote_copied", "another agent cited the same words at a different anchor.")
    elsewhere = sum(
        1 for c in ctx.chunks.values() if c.document == chunk.document and c.anchor != chunk.anchor and quote_in(e.quote, c.text)
    )
    if elsewhere >= 2 or (elsewhere == 1 and len(normalise(e.quote).split()) < LONG_QUOTE_WORDS):
        return fail("quote_repeated", "the same words appear elsewhere in this document, so they don't point here.")
    return None


def check_citations(evidence: Iterable[Evidence], ctx: CitationContext) -> list[CitationFailure]:
    return [f for f in (check_quote(e, ctx) for e in evidence) if f]


def unsupported_numbers(claim: str, evidence: Iterable[Evidence]) -> list[float]:
    """Numbers a finding states that none of its quotes contain."""
    quoted = [n for e in evidence for n in numbers_in(e.quote)]
    return [n for n in numbers_in(claim) if n not in quoted]


def value_in_its_quotes(f: FactValue) -> bool:
    """An extracted value must be what its own quote says, or the rule runs on a number nobody wrote."""
    if f.status != "found":
        return True
    if f.number is not None:
        return any(f.number in numbers_in(e.quote) for e in f.evidence)
    return any(quote_in(f.value or "", e.quote) for e in f.evidence)
