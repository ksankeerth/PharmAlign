from app.chat.gate import arabic_share, needs_english


def test_arabic_question_needs_english():
    assert needs_english("ما هي مدة صلاحية الموافقة التسويقية للدواء؟")


def test_english_with_a_quoted_arabic_term_passes():
    assert not needs_english("What does it cost to bring in raw materials (المواد الخام) under an import permit?")


def test_no_letters_pass():
    assert not needs_english("hi")
    assert not needs_english("")
    assert not needs_english("12345 ???")
    assert arabic_share("") == 0.0


def test_boundary_is_above_thirty_percent():
    assert arabic_share("abcdefg" + "ابج") == 0.3
    assert not needs_english("abcdefg" + "ابج")
    assert needs_english("abcdef" + "ابجد")
