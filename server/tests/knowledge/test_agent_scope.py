"""The WHERE clause an agent search builds from its document and country filters. No database."""
from app.knowledge.search import _agent_scope


def test_no_filter_means_no_where():
    assert _agent_scope(None, None) == (None, "", {})
    assert _agent_scope([], []) == (None, "", {})


def test_documents_only_is_what_it_was():
    docs, where, scope = _agent_scope(["A-01", "A-01", "B-02"], None)
    assert docs == ["A-01", "B-02"]
    assert where == " WHERE document_id = ANY(CAST(:docs AS text[]))" and scope == {"docs": ["A-01", "B-02"]}


def test_countries_ignore_case_spaces_and_duplicates():
    docs, where, scope = _agent_scope(None, [" AE", "ae", "Sa", " "])
    assert docs is None and scope == {"countries": ["ae", "sa"]}
    assert where == " WHERE lower(country) = ANY(CAST(:countries AS text[]))"


def test_both_filters_combine_with_and():
    _, where, scope = _agent_scope(["A-01"], ["AE"])
    assert where == " WHERE document_id = ANY(CAST(:docs AS text[])) AND lower(country) = ANY(CAST(:countries AS text[]))"
    assert set(scope) == {"docs", "countries"}
