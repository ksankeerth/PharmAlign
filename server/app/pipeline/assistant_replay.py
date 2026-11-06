"""The simulation's assistant. It answers with no model: a knowledge question from a search of the seeded knowledge base, and a question about a case
from the case's saved records. Every citation is a real passage with a quote taken from it, so the server's check passes them.
"""
import asyncio
import json
import re
import uuid
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any, AsyncIterator, Mapping, Optional

from pharmalign_contracts.agents.knowledge_assistant import SessionCreate, TurnRequest
from pharmalign_contracts.common import Evidence
from pharmalign_contracts.server.models import CheckDetail, KbHit
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..context import Tenant
from ..domain.fingerprints import locale_sort_key
from ..knowledge.embedding import Embedder, EmbedderUnavailable, get_embedder, terms
from ..knowledge.search import Scope, search
from ..stores import STORES
from ..tables import ROW
from .assistant import AssistantUnavailable
from .citations import CitationContext, check_quote, normalise
from .sources import kb_chunks

CHECKS = STORES["check_result"]
TRACES = STORES["check_trace"]
RECOMMENDATIONS = STORES["recommendation_version"]
DELAY_SECONDS = 0.05

# Asking for advice on promoting or signing. Asking what blocks promotion is fine.
ADVICE = re.compile(r"\b(should|shall|can|could|may|ok|okay|safe|ready)\b.*\b(promot\w*|sign\w*|approve|grant)\b|\brecommend\w*\s+(promot\w*|signing|approval)\b|\badvis\w*\b.*\b(promot\w*|sign\w*)\b")
# Asking it to do something. It only explains.
ACTION = re.compile(r"^\W*(please\s+)?(set|change|update|mark|waive|approve|confirm|send|submit|delete|close|reopen|sign|promote|grant|reject)\b")
OTHER_CASE = re.compile(r"case-\d{4}")
REREAD = re.compile(r"new reading|re-?read|\bread\b.*\bagain\b|look again|fresh")
ABOUT_CASE = re.compile(r"\b(this case|the case|recommend\w*|blocks?|blocking|promot\w*|stand|outstanding|not clear|summary|what is open|what's open)\b")
STATUS_QUESTION = re.compile(r"\b(in force|still valid|superseded|repealed|replaced|current version|legal status|draft|status of)\b")
USAGE = {"type": "usage", "usage": {"model_calls": 1, "tool_calls": 2, "input_tokens": 3200, "output_tokens": 240}}

# Questions the seeded knowledge base answers, offered as follow-ups.
FOLLOW_UPS = (
    "What long-term stability conditions apply in a hot climate?",
    "Who may operate a pharmaceutical establishment?",
    "What does the resolution on tracking and monitoring of drugs require?",
    "How is a product's sameness with a reference approval judged?",
    "What are the requirements for a pharmacovigilance officer?",
    "How should temperature-sensitive medicines be stored and transported?",
    "Is Federal Law No. 8 of 2019 still in force?",
)


def _not_available(reason: str, detail: str) -> dict:
    return {"type": "not_available", "reason": reason, "detail": detail}


def _no_source(detail: str, searches: list[dict], suggestions: list[str]) -> dict:
    return {"type": "no_source", "detail": detail, "searches_tried": searches, "suggestions": suggestions}


def _word(status: str) -> str:
    return status.replace("_", " ")


# ---------------------------------------------------------------- knowledge


@dataclass
class Knowledge:
    """What a knowledge answer reads: the pinned snapshot, the country's shelf and the embedder."""

    snapshot_id: str
    country: Optional[str]
    embedder: Embedder


SENTENCE = re.compile(r"(?<=[.;:!?])\s+")


def _quote_candidates(text: str, query_terms: set[str]) -> list[str]:
    """Whole sentences of the passage, best match to the question first. A quote is always a stretch of the passage itself."""
    flat = " ".join(text.split())
    sentences = [s.strip() for s in SENTENCE.split(flat) if len(s.split()) >= 8]
    if not sentences:
        words = flat.split()
        return [" ".join(words[:40])] if len(words) >= 8 else []
    ranked = sorted(enumerate(sentences), key=lambda p: (-len(query_terms & set(terms(p[1]))), p[0]))
    return [" ".join(s.split()[:45]) for _, s in ranked[:3]]


def _place(hit: KbHit) -> str:
    return f", {hit.article}" if hit.article else f", page {hit.page}" if hit.page else ""


def _best_per_document(hits: list[KbHit], limit: int) -> list[KbHit]:
    seen: set[str] = set()
    picked = []
    for h in hits:
        if h.document_id not in seen:
            seen.add(h.document_id)
            picked.append(h)
        if len(picked) == limit:
            break
    return picked


async def knowledge_answer(db: AsyncSession, tenant_id: str, question: str, kb: Knowledge) -> list[dict]:
    """Searches the pinned snapshot. A question about status searches every status, so each source shows its own."""
    wants_status = bool(STATUS_QUESTION.search(question.lower()))
    scope = Scope(tenant_id, kb.country, kb.snapshot_id, "context" if wants_status else "requirement")
    found = await search(db, scope, question, kb.embedder, top_k=8)
    others = [q for q in FOLLOW_UPS if normalise(q) != normalise(question)]
    if found.no_source_found:
        steps = [
            {"search": t["search"], "candidates": t.get("candidates"),
             "result": t.get("result") or (f"the best match scored {t['best_score']}, below the {t['floor']} a source needs" if t.get("best_score") is not None else "nothing to compare")}
            for t in found.searches_tried
        ]
        return [_no_source("No source in this country's knowledge base answers this, so I won't answer from memory.", steps, others[:3]), USAGE]

    picked = _best_per_document(found.hits, 3)
    chunks = await kb_chunks(db, tenant_id, kb.snapshot_id, [h.anchor for h in picked])
    query_terms = set(terms(question))
    citations: list[Evidence] = []
    cited: list[KbHit] = []
    for hit in picked:
        context = CitationContext({a: replace(c, in_force=True) for a, c in chunks.items()}, question)
        quote = next((q for q in _quote_candidates(hit.text, query_terms) if check_quote(Evidence(anchor=hit.anchor, quote=q), context) is None), None)
        if quote:
            citations.append(Evidence(anchor=hit.anchor, quote=quote))
            cited.append(hit)
    if not cited:
        return [_no_source("Passages match, but none can be quoted with enough context to cite.", [{"search": "meaning and words, inside the filter", "result": "no citable passage"}], others[:3]), USAGE]

    follow = [f"Is {cited[0].title} still in force?"] if not wants_status and len(cited[0].title) <= 70 else []
    text = _knowledge_text(cited, wants_status)
    return [
        {"type": "text_delta", "text": text.split(". ")[0] + ". "},
        {"type": "message", "text": text, "trace_ids": [], "citations": [c.model_dump() for c in citations], "suggestions": (follow + others)[:3]},
        USAGE,
    ]


def _knowledge_text(hits: list[KbHit], by_status: bool) -> str:
    first = hits[0]
    if by_status:
        parts = [f"{h.title} is {_word(h.legal_status)}" for h in hits]
        in_force = [h for h in hits if h.legal_status == "in_force"]
        tail = "Only an in-force source can be relied on as a requirement." if in_force else "None of these is in force, so none can be relied on as a requirement."
        return f"The knowledge base holds {len(hits)} source{'s' if len(hits) != 1 else ''} on this. {'; '.join(parts)}. {tail}"
    text = f"The closest in-force source is {first.title}{_place(first)}."
    rest = [h.title for h in hits[1:]]
    return text + (f" {', '.join(rest)} also bear{'s' if len(rest) == 1 else ''} on it." if rest else "") + " The passages are quoted below, each with its legal status."


# ---------------------------------------------------------------- the case


async def case_answer(db: AsyncSession, tenant_id: str, case_id: str, request: TurnRequest) -> Optional[list[dict]]:
    """Builds the events for a question about the case from what was saved: reasoning, rule traces and the current recommendation. It reads and never writes.
    None means the question isn't about the case."""
    ctx = SimpleNamespace(db=db, tenant=Tenant(tenant_id, "simulation"))
    row = ROW["check_result"]
    checks = sorted(await CHECKS.list(ctx, row.case_id == case_id, row.is_current.is_(True)), key=lambda c: locale_sort_key(c.check_id))
    msg = request.message.lower()
    focus_id = request.focus.check_id if request.focus else None
    focus: Optional[CheckDetail] = next((c for c in checks if c.check_id.lower() in msg), None)
    if focus is None and focus_id:
        focus = next((c for c in checks if focus_id in (c.id, c.check_id)), None)

    other = OTHER_CASE.search(msg)
    if other and other.group(0) != case_id.lower():
        return [_not_available("out_of_scope", f"This chat is scoped to {case_id}. Open {other.group(0).upper()} to ask about it."), USAGE]

    if re.search(r"draft|comment", msg):
        target = focus or next((c for c in checks if c.status in ("held", "look")), None)
        text = f"{target.check_id}: {target.summary or 'Needs review.'} Please confirm against the cited pages before we decide." if target else "Please review the open items before promotion."
        return [{"type": "text_delta", "text": "Here is a draft. It is not posted until a person posts it."}, {"type": "draft_comment", "check_id": target.id if target else None, "text": text}, USAGE]

    if focus:
        if focus.status in ("skipped", "pending", "running", "stale"):
            return [_not_available("not_checked", f"{focus.check_id} has no result to explain: it is {_word(focus.status)}. {focus.summary or ''}".strip()), USAGE]
        if focus.no_source_found:
            step = {"search": "the pack's source for this check, in the case's pinned snapshot", "result": "no in-force source answers this question"}
            return [_no_source(f"{focus.check_id}: no in-force source in this snapshot answers this. I won't guess a requirement. {focus.summary or ''}".strip()[:500], [step], []), USAGE]
        cited = [e.model_dump(mode="json") for e in focus.evidence[:3]]
        if REREAD.search(msg):
            if not cited:
                return [_not_available("not_saved", f"{focus.check_id} cites no pages, so there is nothing to read again."), USAGE]
            quotes = "; ".join(f'"{e["quote"]}"' for e in cited)
            return [{"type": "message", "text": f"This is a new reading made now. It was not part of the check and does not change its status. The cited passages say: {quotes}.", "trace_ids": [], "citations": cited}, USAGE]
        trace = await TRACES.get(ctx, focus.id)
        if trace is None or trace.basis == "not_saved":
            return [_not_available("not_saved", f"No reasoning was saved for {focus.check_id} ({_word(focus.status)}). I won't write an explanation after the fact."), USAGE]
        if trace.basis == "rule_trace" and trace.rule_outcome:
            inputs = ", ".join(f"{k} = {json.dumps(v)}" for k, v in trace.rule_outcome.inputs.items())
            text = f'The rule "{trace.rule_outcome.rule}" was evaluated by code with {inputs}. It {"passed" if trace.rule_outcome.passed else "did not pass"}. {focus.summary or ""}'
            return [{"type": "message", "text": text.strip(), "trace_ids": [focus.id], "citations": cited}, USAGE]
        return [{"type": "message", "text": trace.reasoning or focus.summary or "Saved reasoning is empty.", "trace_ids": [focus.id], "citations": cited}, USAGE]

    if not ABOUT_CASE.search(msg) and not focus_id:
        return None
    rec_row = ROW["recommendation_version"]
    rec = next(iter(await RECOMMENDATIONS.list(ctx, rec_row.case_id == case_id, rec_row.is_current.is_(True))), None)
    if rec is None:
        return [_not_available("not_checked", "The pipeline has not produced a recommendation for this case yet."), USAGE]
    open_checks = [c for c in checks if c.blocking and c.status not in ("pass", "confirmed", "waived")]
    state = f"Not clear: {', '.join(f'{c.check_id} ({_word(c.status)})' for c in open_checks)}." if open_checks else "Every blocking check is clear."
    return [{"type": "message", "text": f'Recommendation v{rec.version} is "{_word(rec.choice)}": {rec.reason} {state}', "trace_ids": [], "citations": [],
             "suggestions": ["What does the knowledge base say about stability?"]}, USAGE]


async def answer_turn(db: AsyncSession, tenant_id: str, case_id: Optional[str], request: TurnRequest, kb: Knowledge) -> list[dict]:
    """The events for one turn. A refusal comes first, then a case question if the chat has a case, then the knowledge base."""
    msg = request.message.lower()
    if ADVICE.search(msg):
        return [_not_available("out_of_scope", "I don't advise on promoting or signing. That is a person's decision. I can show what the knowledge base says, or explain what is on record."), USAGE]
    if ACTION.search(msg):
        return [_not_available("out_of_scope", "I only explain. I can't change a status, waive a check, send anything or sign. A person does that in the case."), USAGE]
    if case_id:
        events = await case_answer(db, tenant_id, case_id, request)
        if events is not None:
            return events
    return await knowledge_answer(db, tenant_id, request.message, kb)


@dataclass
class Open:
    tenant: str
    case_id: Optional[str]
    snapshot_id: str


class ReplayAssistantClient:
    """The simulation's assistant, so a chat works with no model."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], settings, delay_seconds: float = DELAY_SECONDS):
        self.sessions = sessions
        self.settings = settings
        self.delay_seconds = delay_seconds
        self.open_sessions: dict[str, Open] = {}

    async def open(self, session: SessionCreate) -> str:
        agent_session_id = f"asa-{uuid.uuid4().hex[:12]}"
        self.open_sessions[agent_session_id] = Open(session.tenant, session.case_id, session.versions.kb_snapshot)
        return agent_session_id

    async def turn(self, agent_session_id: str, request: TurnRequest) -> AsyncIterator[Mapping[str, Any]]:
        known = self.open_sessions.get(agent_session_id)
        if known is None:
            raise AssistantUnavailable("the agent has no such session")  # a restart forgets sessions, as an agent can
        try:
            embedder = get_embedder(self.settings)
        except EmbedderUnavailable as e:
            raise AssistantUnavailable(str(e)) from e
        async with self.sessions() as db:
            country = await db.scalar(select(ROW["workspace"].country_code).where(ROW["workspace"].id == known.tenant))
            events = await answer_turn(db, known.tenant, known.case_id, request, Knowledge(known.snapshot_id, country, embedder))
        for event in events:
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            yield event

