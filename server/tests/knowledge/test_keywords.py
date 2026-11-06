"""Arabic keyword search: one stem for a word's forms, BM25 on rare words, and fusion of two ranked lists."""
from app.knowledge.keywords import bm25, fuse, stem, tokens, tsquery


def test_word_forms_share_a_stem():
    assert tokens("المنتجات") == tokens("للمنتج") == ["منتج"]


def test_short_words_keep_their_letters():
    assert stem("كتب") == "كتب" and stem("ولد") == "ولد"


def test_english_words_pass_through():
    assert "licence" in tokens("The licence")


def test_question_words_are_not_keywords():
    assert tsquery("هل كم") is None


def test_a_rare_word_outranks_a_common_one():
    rows = [{"chunk_id": str(i), "tokens": "دواء " * 5} for i in range(8)] + [{"chunk_id": "rare", "tokens": "دواء تلوث ميكروبي"}]
    assert bm25("تلوث دواء", rows, total=9)[0]["chunk_id"] == "rare"


def test_no_rows_gives_no_ranking():
    assert bm25("تلوث", [], total=0) == []


def test_fusion_lifts_a_chunk_found_by_both_lists():
    a, b, c = {"chunk_id": "a"}, {"chunk_id": "b"}, {"chunk_id": "c"}
    assert fuse([a, b], [c, b])[0] == b


def test_fusion_keeps_each_chunk_once():
    a = {"chunk_id": "a"}
    assert fuse([a], [a]) == [a]
