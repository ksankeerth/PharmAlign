"""Review like a pull request: comment threads on checks, pages or clauses (open ones block promotion),
and questions to the company (a person always sends them)."""
from pharmalign_contracts.server import models as m

from ..domain.rules import close_question_guards, question_send_guards
from ..ops import op
from ..pipeline import runner
from ..problems import enforce_guards, guard, invalid, not_found, require_reason
from ..tables import ROW
from .support import CHECKS, COMMENTS, QUESTIONS, THREADS, facts_of, get_case, name_of


async def _target_label(ctx, target: m.ThreadTarget) -> str:
    """A check by its pack id, anything else by its reference."""
    if target.type == "check":
        check = await CHECKS.get(ctx, target.ref)
        return check.check_id if check else target.ref
    return "the case" if target.type == "case" else f"{target.type} {target.ref}"


async def _count_open_threads(ctx, check_result_id: str) -> None:
    """The check shows how many open comments it has."""
    check = await CHECKS.get(ctx, check_result_id)
    if check is None or not check.is_current:
        return
    row = ROW["review_thread"]
    open_now = len(await THREADS.list(ctx, row.case_id == check.case_id, row.status == "open", row.target_type == "check", row.target_ref == check.id))
    if open_now != check.open_threads:
        await CHECKS.save(ctx, check.model_copy(update={"open_threads": open_now}))


def _comment(ctx, text: str) -> m.Comment:
    return m.Comment(id=ctx.id("cm"), author_type="person", author_id=ctx.person_id, text=text, at=ctx.now())


def _store_comment(ctx, comment: m.Comment, thread_id: str, case_id: str) -> None:
    """The comment row follows its thread's, so the thread must be flushed first."""
    COMMENTS.add(ctx, comment, thread_id=thread_id, case_id=case_id)


@op("GET", "/api/v1/cases/{case_id}/threads")
async def list_threads(req, ctx):
    await get_case(ctx, req.path.case_id)
    row = ROW["review_thread"]
    where = [row.case_id == req.path.case_id]
    if req.query.status:
        where.append(row.status == req.query.status)
    # Open threads first, newest first within each.
    return await THREADS.page(ctx, req.query, *where, order_by=(row.status, row.created_at.desc()))


@op("POST", "/api/v1/cases/{case_id}/threads")
async def open_thread(req, ctx):
    case = await get_case(ctx, req.path.case_id)
    t = req.body.target
    if t.type == "check" and await CHECKS.get(ctx, t.ref) is None:
        not_found("Check result", t.ref)
    now = ctx.now()
    thread_id = ctx.id("th")
    comment = _comment(ctx, req.body.text)
    thread = m.Thread(id=thread_id, case_id=case.id, target=t, status="open", blocks_promotion=True, comments=[comment], created_at=now, resolved_by=None, resolved_at=None)
    THREADS.add(ctx, thread)
    await ctx.db.flush()
    _store_comment(ctx, comment, thread.id, case.id)
    await ctx.db.flush()
    ctx.timeline(case.id, "comment", f"{await name_of(ctx, ctx.person_id)} commented on {await _target_label(ctx, t)}.", {"type": "person", "id": ctx.person_id}, thread.id)
    ctx.audit("thread_opened", "thread", thread.id, case.id, after={"target": t})
    if t.type == "check":
        await _count_open_threads(ctx, t.ref)
    await runner.refresh_case(ctx, case.id)
    return thread


@op("POST", "/api/v1/threads/{thread_id}/comments")
async def reply(req, ctx):
    thread = await THREADS.require(ctx, req.path.thread_id, "Thread", for_update=True)
    if not req.body.text.strip():
        invalid("A comment needs text.")
    comment = _comment(ctx, req.body.text)
    _store_comment(ctx, comment, thread.id, thread.case_id)
    thread = await THREADS.save(ctx, thread.model_copy(update={"comments": [*thread.comments, comment]}))
    ctx.timeline(thread.case_id, "comment", f"{await name_of(ctx, ctx.person_id)} replied on {await _target_label(ctx, thread.target)}.", {"type": "person", "id": ctx.person_id}, thread.id)
    ctx.audit("comment_added", "thread", thread.id, thread.case_id)
    return thread


@op("POST", "/api/v1/threads/{thread_id}/resolve")
async def resolve(req, ctx):
    thread = await THREADS.require(ctx, req.path.thread_id, "Thread", for_update=True)
    enforce_guards([guard("thread_open", thread.status == "open", "This thread is already resolved.")], "Resolve refused")
    comments = list(thread.comments)
    if req.body.note:
        note = _comment(ctx, req.body.note)
        _store_comment(ctx, note, thread.id, thread.case_id)
        comments.append(note)
    done = await THREADS.save(ctx, thread.model_copy(update={"comments": comments, "status": "resolved", "resolved_by": ctx.person_id, "resolved_at": ctx.now()}))
    ctx.timeline(thread.case_id, "thread_resolved", f"{await name_of(ctx, ctx.person_id)} resolved a comment on {await _target_label(ctx, thread.target)}.", {"type": "person", "id": ctx.person_id}, thread.id)
    ctx.audit("thread_resolved", "thread", thread.id, thread.case_id, before={"status": "open"}, after={"status": "resolved"}, reason=req.body.note)
    await ctx.db.flush()
    if thread.target.type == "check":
        await _count_open_threads(ctx, thread.target.ref)
    await runner.refresh_case(ctx, thread.case_id)
    return done


@op("GET", "/api/v1/cases/{case_id}/questions")
async def list_questions(req, ctx):
    await get_case(ctx, req.path.case_id)
    return await QUESTIONS.page(ctx, req.query, ROW["question"].case_id == req.path.case_id)


@op("POST", "/api/v1/cases/{case_id}/questions")
async def draft_question(req, ctx):
    case = await get_case(ctx, req.path.case_id)
    if not any(t.strip() for t in req.body.texts.values()):
        invalid("A question needs text in at least one language.")
    q = m.Question(id=ctx.id("q"), case_id=case.id, status="draft", check_ids=req.body.check_ids, texts=req.body.texts, drafted_by="person",
                   sent_by=None, sent_at=None, reply_due_at=None, clock_stopped=False)
    QUESTIONS.add(ctx, q)
    ctx.timeline(case.id, "question_drafted", f"{await name_of(ctx, ctx.person_id)} drafted a question.", {"type": "person", "id": ctx.person_id}, q.id)
    ctx.audit("question_drafted", "question", q.id, case.id, after=q)
    return q


@op("PATCH", "/api/v1/questions/{question_id}")
async def edit_question(req, ctx):
    q = await QUESTIONS.require(ctx, req.path.question_id, "Question", for_update=True)
    enforce_guards([guard("draft_only", q.status == "draft", f"The question is {q.status}. Only drafts can be edited.")], "Edit refused")
    done = await QUESTIONS.save(ctx, q.model_copy(update={"texts": req.body.texts}))
    ctx.audit("question_edited", "question", q.id, q.case_id, before=q.texts, after=done.texts)
    return done


@op("POST", "/api/v1/questions/{question_id}/send")
async def send_question(req, ctx):
    q = await QUESTIONS.require(ctx, req.path.question_id, "Question", for_update=True)
    case = await get_case(ctx, q.case_id, for_update=True)
    enforce_guards([
        guard("draft_only", q.status == "draft", f"The question is {q.status}."),
        *question_send_guards(await facts_of(ctx, case), q, req.body.reply_due_at, ctx.now()),
    ], "Sending refused")
    done = await QUESTIONS.save(ctx, q.model_copy(update={
        "status": "sent", "sent_by": ctx.person_id, "sent_at": ctx.now(), "reply_due_at": req.body.reply_due_at, "clock_stopped": True,
    }))
    ctx.timeline(q.case_id, "question_sent", f"{await name_of(ctx, ctx.person_id)} sent a question to the company. The clock stopped.", {"type": "person", "id": ctx.person_id}, q.id)
    ctx.audit("question_sent", "question", q.id, q.case_id, before={"status": "draft"}, after={"status": "sent", "reply_due_at": done.reply_due_at})
    await ctx.db.flush()
    await runner.refresh_case(ctx, q.case_id)
    return done


@op("POST", "/api/v1/questions/{question_id}/close")
async def close_question(req, ctx):
    q = await QUESTIONS.require(ctx, req.path.question_id, "Question", for_update=True)
    reason = require_reason(req.body.reason)
    case = await get_case(ctx, q.case_id, for_update=True)
    enforce_guards(close_question_guards(await facts_of(ctx, case), q, ctx.view, reason), "Close refused")
    done = await QUESTIONS.save(ctx, q.model_copy(update={"status": "closed", "clock_stopped": False}))
    ctx.timeline(q.case_id, "question_closed", f"Question closed: {reason}", {"type": "person", "id": ctx.person_id}, q.id)
    ctx.audit("question_closed", "question", q.id, q.case_id, before={"status": q.status}, after={"status": "closed"}, reason=reason)
    await ctx.db.flush()
    await runner.refresh_case(ctx, q.case_id)
    return done
