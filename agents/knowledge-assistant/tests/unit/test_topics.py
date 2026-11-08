import pytest

from app.chat.topics import TOPICS, suggestions_for


@pytest.mark.parametrize("question, first", [
    ("Are vaping liquids with nicotine allowed?", "How is a medical product classified?"),
    ("Can I advertise a medicine on social media?", "What is needed to license an advertisement for a medical product?"),
    ("What is the deadline for an adverse reaction report?", "What is the deadline for reporting a serious adverse reaction?"),
    ("How do I import a medicine?", "What is needed for a permit to import medical products?"),
])
def test_the_questions_offered_are_about_the_subject_asked(question, first):
    got = suggestions_for([question])
    assert got and got[0] == first and len(got) <= 3


def test_a_question_with_no_subject_gets_none_so_the_general_ones_are_used():
    assert suggestions_for(["How are drone deliveries regulated?"]) == ()


def test_the_question_just_asked_is_never_offered_back():
    got = suggestions_for(["Which medicines are controlled medicines in the UAE?"])
    assert "Which medicines are controlled medicines in the UAE?" not in got and len(got) == 2


def test_every_subject_has_three_questions_and_no_duplicates():
    for pattern, questions in TOPICS:
        assert len(questions) == 3 and len(set(questions)) == 3, pattern.pattern
