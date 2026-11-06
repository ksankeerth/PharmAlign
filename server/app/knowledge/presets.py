"""The ingestion presets the UI offers. Each is a starting profile for a kind of document."""
from pharmalign_contracts.server import models as m


def _p(key: str, label: str, good_for: str, parser: str, split_by: str, words: int, overlap: int, min_words: int, *, annexes: bool = True, images: str = "ignore_and_flag",
       ocr: tuple = (), tables: str = "keep") -> m.IngestPreset:
    return m.IngestPreset(key=key, label=label, good_for=good_for, profile=m.IngestProfile(
        preset=key, parser=parser, include_annexes=annexes, images=images, ocr_languages=list(ocr), tables=tables, split_by=split_by,
        words_per_chunk=words, overlap_words=overlap, min_chunk_words=min_words))


PRESETS = [
    _p("law", "Law or regulation", "Numbered articles. Splits by article so citations stay precise.", "best_layout", "article", 256, 30, 8),
    _p("guideline", "Guideline", "Numbered headings and tables, such as stability or labelling guidance.", "best_layout", "heading", 320, 40, 8),
    _p("web_page", "Web page", "Service cards and portal pages. Removes navigation and footers.", "web_page", "heading", 200, 20, 8, annexes=False, images="ignore_all", tables="flatten"),
    _p("data_table", "Data table", "Registers and product lists. One chunk per row group.", "table", "fixed", 400, 0, 4, annexes=False, images="ignore_all"),
    _p("scanned", "Scanned document", "Image-only pages. Runs OCR and flags pages a person should check.", "best_layout", "heading", 256, 30, 8, images="ocr_image_only", ocr=("en", "ar")),
]
