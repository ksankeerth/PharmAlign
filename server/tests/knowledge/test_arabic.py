"""Arabic repairs: text in reading order stays as it is, and only proven faults change."""
import pytest

from app.knowledge.arabic import clean_line, clean_raw, clean_text, is_arabic, repair_swapped
from app.knowledge.layout import Raw


def raw_of(text: str) -> Raw:
    return Raw([{"text": text, "page": 1}], [], 1, [], [], "")


def test_display_shapes_become_letters():
    assert clean_line("ﻳﺠﺐ أن ﻳﺒﻴﻦ") == "يجب أن يبين"


def test_a_line_in_reading_order_is_not_turned():
    assert clean_line("الإجراء النهائي للمخالفة") == "الإجراء النهائي للمخالفة"


def test_a_backwards_line_is_put_in_reading_order():
    assert clean_line("ﻲﺋﺎﻬﻨﻟا ءاﺮﺟﻹا") == "الإجراء النهائي"


def test_numbers_stay_readable_when_a_line_is_turned():
    assert "12" in clean_line("ﻲﺋﺎﻬﻨﻟا 12 ءاﺮﺟﻹا").split()


def test_kashida_and_fused_digits():
    assert clean_line("مصــدر خلال5 أيام") == "مصدر خلال 5 أيام"


def test_latin_lines_are_untouched():
    assert clean_text("Article (9) of 2024\nStore below 30 C") == "Article (9) of 2024\nStore below 30 C"


@pytest.mark.parametrize("wrong, right", [("املنتج", "المنتج"), ("واملنتجات", "والمنتجات"), ("اإلمارات", "الإمارات"), ("األدوية", "الأدوية"), ("االستيراد", "الاستيراد"), ("خالل", "خلال"), ("ال", "لا")])
def test_swapped_pairs_are_repaired(wrong, right):
    assert repair_swapped(wrong) == right


@pytest.mark.parametrize("word", ["كاملة", "كامل", "الأدوية", "المنتج", "طالب"])
def test_words_that_only_look_swapped_stay(word):
    assert repair_swapped(word) == word


def test_a_clean_document_gets_no_swap_repairs():
    text = "يجب أن يكون التقرير كاملا وأن يبين خالل المرحلة النتائج الدوائية للمستحضر في جميع المراحل " * 30
    assert clean_raw(raw_of(text)).blocks[0]["text"] == text  # the gate sees no fault, so even a swapped-looking word stays


def test_a_flagged_document_gets_them():
    text = "يجب تسجيل املنتج واملنتجات في االستيراد وفق القواعد " * 30
    cleaned = clean_raw(raw_of(text)).blocks[0]["text"]
    assert "المنتج" in cleaned and "املنتج" not in cleaned


def test_language_of_a_text():
    assert is_arabic("ما هي مدة الصلاحية 5") and not is_arabic("What is the shelf life")
