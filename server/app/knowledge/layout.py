"""Layout-aware parsing and stitching for ingestion.

The parsers turn a PDF, HTML or text file into a flat list of typed blocks:

    {"type": "heading" | "paragraph" | "list_item" | "table" | "caption",
     "text": str, "page": int | None, "page_end": int | None, "level": int (headings),
     "rows": list[list[str]] (tables), "caption": str (tables), "stitched": bool}

Images are dropped (PDF image blocks, <img>); repeated page headers/footers, page numbers and
table-of-contents dot leaders are dropped. Text a reader cannot see (white, tiny, off the page, or
hidden by style) is taken out of the blocks and returned separately, so it never reaches search.
stitch() then repairs what a parser gets wrong across page breaks: it joins the pieces of a table
that runs over several pages, attaches a "Table 1: ..." caption to its table, and merges a paragraph
that continues on the next page.
"""
from __future__ import annotations

import csv
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser

ARABIC_RE = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
LATIN_RE = re.compile(r"[A-Za-z]")
DIGITS_AR = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

# legal structure: name -> heading level (1 = part/chapter, 3 = article)
_LEGAL = [
    (re.compile(r"^\s*(?:PART|Part|CHAPTER|Chapter|SECTION|Section|TITLE|Title)\b\s*[\(\[]?\s*[\w٠-٩.-]+", re.I), 1),
    (re.compile(r"^\s*(?:الباب|الفصل|القسم|الجزء)\b"), 1),
    (re.compile(r"^\s*(?:ARTICLE|Article)\s*[\(\[]?\s*([\d٠-٩]+)"), 3),
    (re.compile(r"^\s*(?:المادة|مادة)\s*[\(\[]?\s*([\d٠-٩]+)"), 3),
]
_CAPTION_RE = re.compile(r"^\s*(?:Table|Figure|Fig\.|Annex(?:ure)?|Appendix)\s*[\dA-Z]+[\s:.\-–]|^\s*(?:جدول|شكل)\s*[\d٠-٩]+", re.I)
_DOT_LEADER_RE = re.compile(r"(?:\.\s*){8,}|(?:…\s*){4,}")
_END_PUNCT = tuple(".:;?!)\"'»؛؟۔")


def script_stats(text: str) -> tuple[int, int]:
    return len(ARABIC_RE.findall(text)), len(LATIN_RE.findall(text))


def legal_level(text: str) -> tuple[int | None, str]:
    """(heading level, article number) if the text starts like a legal heading."""
    for rx, lvl in _LEGAL:
        m = rx.match(text)
        if m:
            art = m.group(1).translate(DIGITS_AR) if lvl == 3 and m.groups() else ""
            return lvl, art
    return None, ""


def _clean(s: str) -> str:
    s = re.sub(r"-\n(?=[a-z])", "", s)  # de-hyphenate across line ends
    return re.sub(r"\s+", " ", s).strip()


def _block(type_, text, page=None, **kw):
    return {"type": type_, "text": text, "page": page, "page_end": page, "level": kw.pop("level", 0),
            "rows": kw.pop("rows", None), "caption": "", "stitched": False, **kw}


@dataclass
class Hidden:
    """Text a reader cannot see. Kept apart from the blocks, so it never reaches search."""

    page: int | None
    reason: str  # near_white, tiny, off_page, transparent, hidden_style, comment
    text: str


@dataclass
class Options:
    """What the ingestion profile asks of a parser."""

    pages: set[int] | None = None  # None: every page
    headers_footers: bool = True  # remove repeated page headers and footers
    page_numbers: bool = True
    contents: bool = True  # remove table-of-contents lines
    ocr_languages: list[str] = field(default_factory=list)
    ocr: bool = False
    allow_arabic: bool = False  # the fast parser reads Arabic letters wrongly; only a quality check may ask for it anyway


@dataclass
class Raw:
    blocks: list[dict]
    warnings: list[str]
    page_count: int = 0
    image_only: list[int] = field(default_factory=list)
    hidden: list[Hidden] = field(default_factory=list)
    meta_text: str = ""  # metadata and annotations, scanned for instructions but never read as content


class ArabicNotSupported(Exception):
    """The fast parser met an Arabic page. It is not used for Arabic."""

    def __init__(self, page: int):
        super().__init__(f"Page {page} has Arabic text. The fast parser is not used for Arabic: choose Best layout with Docling installed.")
        self.page = page


def parse_pages(spec: str | None, total: int) -> set[int] | None:
    """'1-3,7' -> {1, 2, 3, 7}, kept to the pages that exist."""
    if not spec:
        return None
    pages: set[int] = set()
    for part in spec.split(","):
        a, _, b = part.partition("-")
        pages.update(range(int(a), int(b or a) + 1))
    return {p for p in pages if 1 <= p <= max(total, 1)}


# --------------------------------------------------------------------------- PDF

_LIST_START_RE = re.compile(r"^\s*(?:[-•▪●]|\(?[a-zA-Z0-9ivx]{1,3}[.)])\s+\S")


def _merge_lines(items: list[dict]) -> list[dict]:
    """Some PDFs store every line as its own block: join tight, same-style, aligned lines into one paragraph."""
    out: list[dict] = []
    for it in items:
        p = out[-1] if out else None
        if (p and it["kind"] == p["kind"] == "text" and it["page"] == p["page"]
                and abs(it["size"] - p["size"]) < 0.4 and it["bold"] == p["bold"]
                and -1 <= it["y0"] - p["y1"] <= 0.45 * it["size"]
                and (abs(it["x0"] - p["x0"]) <= 2 * it["size"] or abs(it["x1"] - p["x1"]) <= 2 * it["size"])
                and not _LIST_START_RE.match(it["text"])
                and not legal_level(it["text"])[0] and not legal_level(p["text"])[0]):
            p["text"] += " " + it["text"]
            p["y1"], p["x0"], p["x1"] = it["y1"], min(p["x0"], it["x0"]), max(p["x1"], it["x1"])
        else:
            out.append(dict(it))
    return out

def _hidden_reason(span: dict, rect, dark_page: bool) -> str | None:
    """Why a text span cannot be seen, or None."""
    x0, y0, x1, y1 = span["bbox"]
    if x1 < rect.x0 - 2 or x0 > rect.x1 + 2 or y1 < rect.y0 - 2 or y0 > rect.y1 + 2:
        return "off_page"
    if span["size"] < 2.0:
        return "tiny"
    if span.get("alpha", 255) < 25:
        return "transparent"
    c = span.get("color", 0)
    if not dark_page and 0.299 * ((c >> 16) & 255) + 0.587 * ((c >> 8) & 255) + 0.114 * (c & 255) > 245:
        return "near_white"
    return None


def parse_pdf_pymupdf(data: bytes, opts: Options | None = None) -> Raw:
    """The fast parser. Reads Latin text well. For Arabic it reads some letters wrongly, so it refuses an Arabic page."""
    import pymupdf
    opts = opts or Options()
    warnings: list[str] = []
    raw_pages: list[list[dict]] = []
    image_only: list[int] = []
    hidden: list[Hidden] = []
    meta: list[str] = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        n_pages = len(doc)
        meta.extend(v for v in (doc.metadata or {}).values() if isinstance(v, str))
        for pno, page in enumerate(doc, 1):
            for annot in page.annots() or []:
                meta.append((annot.info or {}).get("content", ""))
            if opts.pages is not None and pno not in opts.pages:
                continue
            H = page.rect.height or 1.0
            try:  # a dark background makes white text normal
                dark_page = any(
                    d.get("fill") and sum(d["fill"][:3]) < 0.6 and d["rect"].width * d["rect"].height > 0.4 * page.rect.width * H
                    for d in page.get_drawings()
                )
            except Exception:  # noqa: BLE001
                dark_page = False
            try:  # ruled-line detection only: PyMuPDF 2.x's ONNX layout model is slow and can crash
                tables = page.find_tables(use_layout=False).tables
            except TypeError:  # older PyMuPDF has no use_layout argument
                tables = page.find_tables().tables
            except Exception:  # noqa: BLE001
                tables = []
            items: list[dict] = []
            boxes = []
            for t in tables:
                rows = [[_clean(c or "") for c in r] for r in t.extract()]
                rows = [r for r in rows if any(r)]
                if len(rows) >= 2 and max(len(r) for r in rows) >= 2:
                    boxes.append(t.bbox)
                    items.append({"kind": "table", "rows": rows, "y0": t.bbox[1], "y1": t.bbox[3], "H": H})
            words_on_page = sum(len(" ".join(r).split()) for it in items for r in it["rows"])  # table text counts
            for b in page.get_text("dict", sort=True)["blocks"]:
                if b["type"] != 0:  # image block: dropped
                    continue
                x0, y0, x1, y1 = b["bbox"]
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                if any(bx[0] <= cx <= bx[2] and bx[1] <= cy <= bx[3] for bx in boxes):
                    continue  # inside a detected table: the table block carries this text
                lines, chars, size_sum, bold = [], 0, 0.0, 0
                for ln in b["lines"]:
                    spans = []
                    for sp in ln["spans"]:
                        why = _hidden_reason(sp, page.rect, dark_page) if sp["text"].strip() else None
                        if why:
                            hidden.append(Hidden(pno, why, sp["text"].strip()))
                        else:
                            spans.append(sp)
                    lines.append("".join(sp["text"] for sp in spans))
                    for s in spans:
                        c = len(s["text"].strip())
                        chars += c
                        size_sum += s["size"] * c
                        if (s["flags"] & 16) or "bold" in s["font"].lower():
                            bold += c
                text = _clean("\n".join(lines))
                if not text or not chars:
                    continue
                words_on_page += len(text.split())
                items.append({"kind": "text", "text": text, "size": size_sum / chars, "bold": bold / chars > 0.6,
                              "y0": y0, "y1": y1, "x0": x0, "x1": x1, "H": H})
            if words_on_page < 15 and page.get_images():
                image_only.append(pno)
                warnings.append(f"p{pno} image-only")
            ar = sum(len(ARABIC_RE.findall(i.get("text", ""))) for i in items)
            lat = sum(len(LATIN_RE.findall(i.get("text", ""))) for i in items)
            if ar >= 20 and not opts.allow_arabic:
                raise ArabicNotSupported(pno)
            rtl = ar > lat
            # rows of ~6pt, then left-to-right (right-to-left on Arabic pages): keeps "term | definition" order
            items.sort(key=lambda i: (round(i["y0"] / 6), -i.get("x0", 0) if rtl else i.get("x0", 0)))
            for it in items:
                it["page"] = pno
            raw_pages.append(items)

    # repeated page headers/footers (same text in the top/bottom margin on many pages) and page numbers
    margin = Counter()
    for items in raw_pages:
        for it in items:
            if it["kind"] == "text" and (it["y0"] / it["H"] < 0.08 or it["y1"] / it["H"] > 0.92):
                margin[re.sub(r"\d+", "#", it["text"].lower())] += 1
    repeated = {k for k, v in margin.items() if opts.headers_footers and n_pages >= 4 and v >= max(3, 0.3 * n_pages)}

    kept: list[dict] = []
    for items in raw_pages:
        for it in items:
            if it["kind"] == "text":
                in_margin = it["y0"] / it["H"] < 0.08 or it["y1"] / it["H"] > 0.92
                if in_margin and (re.sub(r"\d+", "#", it["text"].lower()) in repeated
                                  or (opts.page_numbers and re.fullmatch(r"[-–\s\d/]*(?:page|صفحة)?\s*[-–\s\d/ofمن]*", it["text"].lower()))):
                    continue
                if opts.contents and _DOT_LEADER_RE.search(it["text"]):  # table-of-contents line
                    continue
            kept.append(it)

    kept = _merge_lines(kept)

    # body font size = most common size, weighted by words
    sizes = Counter()
    for it in kept:
        if it["kind"] == "text":
            sizes[round(it["size"] * 2) / 2] += len(it["text"].split())
    body = sizes.most_common(1)[0][0] if sizes else 10.0
    heading_sizes = sorted({round(it["size"] * 2) / 2 for it in kept if it["kind"] == "text"
                            and it["size"] >= body + 1.5 and len(it["text"].split()) <= 25}, reverse=True)

    blocks: list[dict] = []
    for it in kept:
        if it["kind"] == "table":
            blocks.append(_block("table", "", it["page"], rows=it["rows"], top=it["y0"] / it["H"],
                                 bottom=it["y1"] / it["H"]))
            continue
        text, nwords = it["text"], len(it["text"].split())
        lvl, _ = legal_level(text)
        sz = round(it["size"] * 2) / 2
        if lvl and nwords <= 30:
            blocks.append(_block("heading", text, it["page"], level=lvl))
        elif sz in heading_sizes and nwords <= 25:
            blocks.append(_block("heading", text, it["page"], level=min(heading_sizes.index(sz) + 1, 4)))
        elif it["bold"] and nwords <= 12 and not text.endswith(_END_PUNCT):
            blocks.append(_block("heading", text, it["page"], level=4))
        elif _CAPTION_RE.match(text) and nwords <= 40:
            blocks.append(_block("caption", text, it["page"]))
        elif re.match(r"^\s*(?:[-•▪●]|\(?[a-zA-Z0-9ivx]{1,3}[.)])\s+\S", text) and nwords < 80:
            blocks.append(_block("list_item", text, it["page"]))
        else:
            blocks.append(_block("paragraph", text, it["page"], top=it["y0"] / it["H"], bottom=it["y1"] / it["H"]))
    return Raw(blocks, warnings, n_pages, image_only, hidden, " ".join(meta))



# --------------------------------------------------------------------------- PDF via Docling

_DOCLING: dict = {}
_NUMBERED_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)[.)]?\s+\S")


def docling_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("docling") is not None


def _docling_converter(ocr: bool, languages: tuple[str, ...]):
    """One converter per setting and process. With OCR off, pictures and scanned pages are dropped, not read."""
    key = (ocr, languages)
    if key not in _DOCLING:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
        opts = PdfPipelineOptions(do_ocr=ocr, do_table_structure=True)
        if ocr and languages:
            opts.ocr_options.lang = list(languages)
        opts.generate_picture_images = False
        opts.generate_page_images = False
        _DOCLING[key] = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    return _DOCLING[key]


def _heading_level(text: str, docling_level: int) -> int:
    lvl, _ = legal_level(text)
    if lvl:
        return lvl
    m = _NUMBERED_RE.match(text)  # "2.1.3 Title" -> 3
    if m:
        return min(m.group(1).count(".") + 1, 4)
    return min(max(docling_level, 1), 4)


def parse_pdf_docling(data: bytes, opts: Options | None = None) -> Raw:
    """Layout models, and the parser for Arabic pages. It reads the same files the fast parser garbles."""
    from io import BytesIO

    from docling.datamodel.base_models import DocumentStream
    opts = opts or Options()
    converter = _docling_converter(opts.ocr, tuple(opts.ocr_languages))
    doc = converter.convert(DocumentStream(name="document.pdf", stream=BytesIO(data))).document
    blocks: list[dict] = []
    text_pages: set[int] = set()
    for it, lvl in doc.iterate_items():
        label = getattr(it.label, "value", str(it.label))
        prov = it.prov[0] if getattr(it, "prov", None) else None
        page = prov.page_no if prov else None
        if label in ("page_header", "page_footer", "picture", "document_index", "chart"):
            continue  # headers/footers, images and the table of contents are dropped
        if label == "table":
            rows = [[_clean(c.text) for c in row] for row in it.data.grid]
            rows = [r for r in rows if any(r)]
            if len(rows) < 1 or max((len(r) for r in rows), default=0) < 1:
                continue
            top = bottom = None
            if prov:
                H = doc.pages[page].size.height
                bb = prov.bbox.to_top_left_origin(page_height=H)
                top, bottom = bb.t / H, bb.b / H
            cap = ""
            try:
                cap = _clean(it.caption_text(doc))
            except Exception:  # noqa: BLE001
                pass
            blocks.append(_block("table", "", page, rows=rows, top=top, bottom=bottom))
            if cap:
                blocks[-1]["caption"] = cap
            if page:
                text_pages.add(page)
            continue
        text = _clean(getattr(it, "text", "") or "")
        if not text:
            continue
        if page:
            text_pages.add(page)
        if label in ("section_header", "title"):
            blocks.append(_block("heading", text, page, level=_heading_level(text, getattr(it, "level", 1) or 1)))
        elif label == "list_item" or label.startswith("checkbox"):
            blocks.append(_block("list_item", text, page))
        elif label == "caption" or (_CAPTION_RE.match(text) and len(text.split()) <= 40):
            blocks.append(_block("caption", text, page))
        else:
            lv, _ = legal_level(text)
            blocks.append(_block("heading", text, page, level=lv) if lv and len(text.split()) <= 30
                          else _block("paragraph", text, page))
    pic_pages = {p.page_no for it, _ in doc.iterate_items() if getattr(it.label, "value", "") == "picture"
                 for p in (getattr(it, "prov", None) or [])}
    image_only = sorted(pic_pages - text_pages)
    if opts.pages is not None:
        blocks = [b for b in blocks if b["page"] is None or b["page"] in opts.pages]
    return Raw(blocks, [f"p{p} image-only" for p in image_only], len(doc.pages), image_only)

# --------------------------------------------------------------------------- HTML

_VOID = {"br", "img", "hr", "meta", "link", "input", "source", "wbr", "area", "base", "col", "embed", "param", "track"}
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?:px|pt|em|rem|%)?\s*(?:;|$)|opacity\s*:\s*0(?:\.0+)?\s*(?:;|$)"
    r"|(?:left|top|text-indent)\s*:\s*-\d{3,}|(?<![-\w])color\s*:\s*(?:#fff(?:fff)?|white)\b", re.I)


class _HtmlBlocks(HTMLParser):
    SKIP = {"script", "style", "noscript", "head", "nav", "footer", "aside", "form", "svg", "template"}
    BLOCK = {"p", "div", "li", "section", "article", "blockquote", "pre", "dd", "dt", "figcaption", "caption"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict] = []
        self._skip = 0
        self._buf: list[str] = []
        self._tag: str | None = None
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._open: list[str] = []  # open tags
        self._hide = 0  # depth of the open tag that hides what is inside it
        self.hidden: list[Hidden] = []

    def handle_comment(self, data):
        if data.strip():
            self.hidden.append(Hidden(None, "comment", data.strip()))

    def _flush(self):
        text = _clean("".join(self._buf))
        self._buf = []
        if not text:
            return
        t = self._tag or "p"
        if t.startswith("h") and len(t) == 2 and t[1].isdigit():
            self.blocks.append(_block("heading", text, level=min(int(t[1]), 4)))
        elif t == "li":
            self.blocks.append(_block("list_item", text))
        elif t in ("caption", "figcaption") or (_CAPTION_RE.match(text) and len(text.split()) <= 40):
            self.blocks.append(_block("caption", text))
        else:
            lvl, _ = legal_level(text)
            self.blocks.append(_block("heading", text, level=lvl) if lvl and len(text.split()) <= 30
                               else _block("paragraph", text))

    def handle_starttag(self, tag, attrs):
        if tag not in _VOID:
            self._open.append(tag)
            a = dict(attrs)
            if not self._hide and ("hidden" in a or (a.get("aria-hidden") or "").lower() == "true" or _HIDDEN_STYLE.search(a.get("style") or "")):
                self._flush()
                self._hide = len(self._open)
        if self._hide:
            return
        if tag in self.SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "table":
            self._flush()
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag in self.BLOCK or (tag.startswith("h") and len(tag) == 2 and tag[1].isdigit()):
            self._flush()
            self._tag = tag
        elif tag == "br" and self._cell is None:
            self._flush()

    def handle_endtag(self, tag):
        if tag in self._open:
            while self._open and self._open.pop() != tag:
                pass
            if self._hide and len(self._open) < self._hide:
                self._hide = 0
                return
        if self._hide:
            return
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(_clean("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if len(self._table) >= 2 and max(len(r) for r in self._table) >= 2:
                self.blocks.append(_block("table", "", rows=self._table))
            elif self._table:  # layout table with one column: keep the text
                for r in self._table:
                    self.blocks.append(_block("paragraph", " ".join(r)))
            self._table = None
        elif tag in self.BLOCK or (tag.startswith("h") and len(tag) == 2 and tag[1].isdigit()):
            self._flush()
            self._tag = None

    def handle_data(self, data):
        if self._hide:
            if data.strip():
                self.hidden.append(Hidden(None, "hidden_style", data.strip()))
            return
        if self._skip:
            return
        if self._cell is not None:
            self._cell.append(data)
        else:
            self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


def parse_html(data: bytes, opts: Options | None = None) -> Raw:
    p = _HtmlBlocks()
    p.feed(data.decode("utf-8", errors="replace"))
    p.close()
    return Raw(p.blocks, [], 1, [], p.hidden)


# --------------------------------------------------------------------------- text / md / csv

def parse_text(data: bytes, filename: str = "", opts: Options | None = None) -> Raw:
    raw = data.decode("utf-8", errors="replace")
    if filename.lower().endswith(".csv"):
        rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(raw)) if any(c.strip() for c in r)]
        return Raw([_block("table", "", rows=rows)] if len(rows) >= 2 else [], [], 1)
    blocks = []
    for para in re.split(r"\n\s*\n", raw):
        text = _clean(para)
        if not text:
            continue
        m = re.match(r"^(#{1,6})\s+(.*)", para.strip())
        if m:
            blocks.append(_block("heading", _clean(m.group(2)), level=min(len(m.group(1)), 4)))
        else:
            lvl, _ = legal_level(text)
            blocks.append(_block("heading", text, level=lvl) if lvl and len(text.split()) <= 30
                          else _block("paragraph", text))
    return Raw(blocks, [], 1)


# --------------------------------------------------------------------------- stitching

def _numeric(cell: str) -> bool:
    return bool(re.fullmatch(r"[\d\s.,%/()\-+]*", cell or "")) and bool(re.search(r"\d", cell or ""))


def has_header(rows: list[list[str]]) -> bool:
    """First row is a header if it is filled in and not mostly numbers."""
    if re.fullmatch(r"\d+[.)]?", (rows[0][0] or "").strip()):  # "2." / "3)" row number: a data row
        return False
    cells = [c for c in rows[0] if c]
    return len(cells) >= max(2, len(rows[0]) // 2) and sum(_numeric(c) for c in cells) <= len(cells) // 3


def stitch(blocks: list[dict]) -> list[dict]:
    """Join table pieces across pages, attach captions to tables, merge paragraphs split by a page break."""
    out: list[dict] = []
    for b in blocks:
        prev = out[-1] if out else None
        if b["type"] == "table" and prev and prev["type"] == "table" and b["page"] is not None \
                and prev["page_end"] is not None and 0 <= b["page"] - prev["page_end"] <= 1 \
                and len(b["rows"][0]) == len(prev["rows"][0]) and not b.get("caption") \
                and (b["page"] == prev["page_end"]
                     or (prev.get("bottom") is None or prev["bottom"] > 0.6) and (b.get("top") is None or b["top"] < 0.4)):
            rows = b["rows"]
            if has_header(prev["rows"]) and rows[0] == prev["rows"][0]:  # header repeated on the new page
                rows = rows[1:]
            prev["rows"] = prev["rows"] + rows
            prev["page_end"], prev["stitched"] = b["page_end"], True
            prev["bottom"] = b.get("bottom")
            continue
        if b["type"] == "paragraph" and prev and prev["type"] == "paragraph" and b["page"] is not None \
                and b["page"] == prev["page_end"] + 1 and not prev["text"].endswith(_END_PUNCT) \
                and (b["text"][:1].islower() or ARABIC_RE.match(b["text"][:1] or " ")):
            prev["text"] += " " + b["text"]
            prev["page_end"], prev["stitched"] = b["page_end"], True
            continue
        out.append(b)

    # captions: "Table 1: ..." right before or after a table belongs to that table
    res: list[dict] = []
    i = 0
    while i < len(out):
        b = out[i]
        if b["type"] == "caption":
            nxt = out[i + 1] if i + 1 < len(out) else None
            prv = res[-1] if res else None
            if nxt and nxt["type"] == "table" and not nxt["caption"]:
                nxt["caption"] = b["text"]
                i += 1
                continue
            if prv and prv["type"] == "table" and not prv["caption"]:
                prv["caption"] = b["text"]
                i += 1
                continue
            b = {**b, "type": "paragraph"}  # caption of a figure: keep as text
        res.append(b)
        i += 1
    return res
