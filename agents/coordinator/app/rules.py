"""What is specific to the Coordinator: the first message and the rules a brief must keep before it leaves."""
import json
from functools import lru_cache
from typing import Annotated

from pharmalign_agent_common import ToolLog, check_evidence
from pharmalign_agent_common.evidence import unquoted_numbers
from pharmalign_contracts.agents.run_agents import BriefRequest, BriefResult, DraftQuestion
from pharmalign_contracts.common import Identifier, Strict
from pydantic import BaseModel, ConfigDict, Field, create_model

QuestionText = Annotated[str, Field(min_length=1, max_length=2000)]


def schema_for(request: BriefRequest) -> type[BaseModel]:
    """The result schema the model is given for this run: each question's texts written as one field per output language.

    The contract's `texts` is a map by language. Gemini's structured output can't fill a map with open keys (it returns
    `{}`), so every brief with a question was refused. The answer is still parsed and checked as BriefResult.
    """
    return _schema(tuple(dict.fromkeys(request.output_languages)))


@lru_cache(maxsize=16)
def _schema(languages: tuple[str, ...]) -> type[BaseModel]:
    # Field names can't hold every code ("en-GB"), so each field carries its code as an alias, and is written back by it.
    texts = create_model(
        "QuestionTexts", __config__=ConfigDict(extra="forbid", serialize_by_alias=True),
        **{f"t_{i}": (QuestionText, Field(alias=code)) for i, code in enumerate(languages)},
    )
    question = create_model(
        "DraftQuestion", __base__=Strict, __doc__=DraftQuestion.__doc__,
        check_ids=(list[Identifier], Field(min_length=1)),
        texts=(texts, Field(description="One text in each output language.")),
    )
    return create_model("BriefResult", __base__=BriefResult, draft_questions=(list[question], Field(default_factory=list)))


def build_message(request: BriefRequest) -> str:
    """The run's input for the model."""
    task = request.model_dump(
        mode="json",
        include={
            "language", "as_of", "case", "input_fingerprint", "results", "conflicts", "open_comments",
            "previous_recommendation", "new_event", "recommendation_options", "output_languages", "previous_rejection",
        },
    )
    return json.dumps(task, ensure_ascii=False)


def check_result(request: BriefRequest, result: BriefResult, log: ToolLog) -> None:
    """Mirrors the contract and the server's checks. Raises ValueError."""
    if result.input_fingerprint != request.input_fingerprint:
        raise ValueError(f"input_fingerprint must be {request.input_fingerprint}, copied unchanged")
    choice = result.recommendation.choice
    if choice not in request.recommendation_options:
        raise ValueError(f"choice '{choice}' wasn't offered. Choose one of: {', '.join(request.recommendation_options)}")
    check_evidence(result, log)  # a brief carries no quotes, so this holds if one is ever added
    known = {r.check_id for r in request.results}
    unknown = [c for c in result.recommendation.reason_evidence if c not in known]
    if unknown:
        raise ValueError(f"reason_evidence names {unknown[0]}, which is not a finding in results. Cite check ids from results")
    languages = set(request.output_languages)
    for q in result.draft_questions:
        unknown = [c for c in q.check_ids if c not in known]
        if unknown:
            raise ValueError(f"a question names {unknown[0]}, which is not a finding in results. Tie each question to checks in results")
        if set(q.texts) != languages:
            raise ValueError(f"each question needs a text in exactly these languages: {', '.join(request.output_languages)}")
    texts = [result.summary, result.recommendation.reason, *(t for q in result.draft_questions for t in q.texts.values())]
    allowed = [build_message(request), *log.texts()]  # numbers the findings, comments and tools already hold
    for text in texts:
        extra = unquoted_numbers(text, [], allowed)
        if extra:
            raise ValueError(f"the brief states {extra[0]:g}, which no finding, comment or tool result contains")
