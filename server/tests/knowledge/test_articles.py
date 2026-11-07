"""Article headings, English and Arabic: which lines are headings, and how parsed blocks are marked so chunks carry the article."""
import pytest

from app.knowledge.articles import mark_articles, promote, split_inline
from app.knowledge.ingest import build_chunks
from app.knowledge.layout import Raw, legal_level
from app.knowledge.presets import PRESETS
from app.knowledge.quality import article_numbers

LAW = next(p for p in PRESETS if p.key == "law").profile


def block(type_, text, page=1, level=0):
    return {"type": type_, "text": text, "page": page, "page_end": page, "level": level, "rows": None, "caption": "", "stitched": False}


@pytest.mark.parametrize("line,article", [
    ("Article (5) Definitions", "5"),
    ("Article 12", "12"),
    ("ARTICLE 13.2 Cooperation between the authorities", "13.2"),
    ("Article 10a", "10a"),
    ("Article 7: Scope", "7"),
])
def test_english_article_headings(line, article):
    assert legal_level(line) == (3, article)


@pytest.mark.parametrize("line", [
    "Article 10(1) of Regulation (EU) No 1025/2012; or",
    "Article 11;",
    "Article 11 have been drawn up",
    "Article 4, and the annexes",
    "Article 9 shall apply",
])
def test_english_references_are_not_headings(line):
    assert legal_level(line) == (None, "")


def test_english_parts_and_chapters_are_level_one():
    assert legal_level("CHAPTER II General provisions") == (1, "")
    assert legal_level("Part 3") == (1, "")
    assert legal_level("Schedule No. (1) Attached to Cabinet Resolution No. (40) of 2025 Regarding the Fees") == (1, "")
    assert legal_level("Schedule of fees is set by the Cabinet.") == (None, "")
    assert legal_level("Some ordinary sentence.") == (None, "")


@pytest.mark.parametrize("line,article", [
    ("املادة(1)", "1"),
    ("( املادة3)", "3"),
    ("المادة (4)", "4"),
    ("المادة ٦", "6"),
    ("املادة األولى", "1"),
    (":المادة الثانية", "2"),
    ("المادة الحادية عشرة", "11"),
    ("المادة الثانية والعشرون:", "22"),
    ("المادة المائة", "100"),
    ("المادة الخامسة بعد المائة", "105"),
    ("مادة (12) - التعريفات", "12"),
])
def test_arabic_article_headings(line, article):
    assert legal_level(line) == (3, article)


def test_an_arabic_reference_inside_a_sentence_is_not_a_heading():
    assert legal_level("المادة (5) من هذا القانون تنص على أن يلتزم المرخص له بما يلي") == (None, "")


def test_a_long_arabic_line_is_a_heading_when_a_colon_follows_the_number():
    assert legal_level("المادة (5): يلتزم المرخص له بما يلي من الأحكام الواردة في هذا القانون") == (3, "5")


def test_arabic_chapters_are_level_one():
    assert legal_level("الفصل الثاني") == (1, "")
    assert legal_level("الباب الأول أحكام عامة") == (1, "")


def test_split_inline_cuts_at_the_heading_form():
    out = split_inline([block("paragraph", ".التقرير ( المادة 10 ) المسؤولية المشتركة يكون الأطراف مسؤولين")])
    assert [(b["type"], b["text"]) for b in out] == [
        ("paragraph", ".التقرير"), ("heading", "المادة (10)"), ("paragraph", "المسؤولية المشتركة يكون الأطراف مسؤولين")]
    assert out[1]["level"] == 3


def test_split_inline_handles_several_marks_and_arabic_indic_digits():
    out = split_inline([block("paragraph", "( المادة ١ ) نص أول ( المادة 2 ) نص ثان")])
    assert [(b["type"], b["text"]) for b in out] == [
        ("heading", "المادة (1)"), ("paragraph", "نص أول"), ("heading", "المادة (2)"), ("paragraph", "نص ثان")]


def test_split_inline_leaves_a_reference_alone():
    blocks = [block("paragraph", "المادة (5) من هذا القانون")]
    assert split_inline(blocks) == blocks


def test_split_inline_leaves_english_alone():
    blocks = [block("paragraph", "See (Article 5) for details")]
    assert split_inline(blocks) == blocks


def test_promote_turns_a_leading_article_line_into_a_heading():
    out = promote([block("paragraph", "Article (7)\nThe supplier shall keep records of every batch.")])
    assert [(b["type"], b["text"]) for b in out] == [("heading", "Article (7)"), ("paragraph", "The supplier shall keep records of every batch.")]
    assert out[0]["level"] == 3


def test_promote_keeps_other_paragraphs():
    blocks = [block("paragraph", "As set out in Article 7;\nmore text"), block("paragraph", "Plain text.")]
    assert promote(blocks) == blocks


def test_end_to_end_chunks_carry_the_article():
    body = "The supplier shall keep records of every batch placed on the market and make them available to the authority. " * 2
    raw = Raw([
        block("heading", "Chapter 1 General", level=1),
        block("paragraph", "Article (1) Definitions\n" + body),
        block("paragraph", "ARTICLE 2.1 Scope\n" + body),
        block("paragraph", "Article 3\n" + body),
        block("paragraph", "الفصل الأول", level=1),
        block("paragraph", "المادة الرابعة\n" + "يلتزم المرخص له بحفظ سجلات كل دفعة تطرح في السوق وإتاحتها للهيئة عند الطلب. " * 3),
        block("paragraph", "مقدمة سابقة ( المادة 5 ) " + "يجب على المورد إبلاغ الهيئة عن أي حادث خلال خمسة أيام من علمه به. " * 3),
    ], [], 1, [], [], "")
    chunks, _ = build_chunks(mark_articles(raw), LAW, "en", 4000)
    assert [c["article"] for c in chunks if c["article"]] == ["1", "2.1", "3", "4", "5"]
    assert article_numbers(chunks) == [1, 2, 3, 4, 5]


def pdf_with(lines: list[tuple[str, str]]) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    for n, (font, text) in enumerate(lines):
        page.insert_text((72, 72 + 40 * n), text, fontsize=11, fontname=font)  # a gap between lines, so each is its own block
    return doc.tobytes()


def test_a_short_bold_title_that_ends_in_a_colon_is_a_heading_but_the_end_of_a_bold_sentence_is_not():
    from app.knowledge.layout import Options, parse_pdf_pymupdf

    raw = parse_pdf_pymupdf(pdf_with([
        ("hebo", "The Reference Countries:"),
        ("helv", "The list below names the countries whose authority counts as a reference, and the product has to meet the criteria."),
        ("hebo", "A. To be registered with the competent authority in at least one of"),
        ("hebo", "these countries:"),
        ("helv", "1. Australia"),
    ]), Options())
    headings = [b["text"] for b in raw.blocks if b["type"] == "heading"]
    assert headings == ["The Reference Countries:"]
