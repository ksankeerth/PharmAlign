"""Errors, as RFC 9457 problem JSON with a stable code. Mirrors the helpers in the mock's core.ts."""
from typing import Iterable, NoReturn, Optional

from fastapi.responses import JSONResponse
from pharmalign_contracts.common import Problem
from pharmalign_contracts.server.models import GuardProblem, GuardResult

MEDIA_TYPE = "application/problem+json"
MAX_TEXT = 500


class ApiError(Exception):
    """Raised anywhere in a request. Rolls the request back and becomes the response."""

    def __init__(self, body: Problem):
        super().__init__(f"{body.status} {body.code}")
        self.body = body


def _short(text: Optional[str]) -> Optional[str]:
    return text[:MAX_TEXT] if text else None


def problem(status: int, code: str, title: str, detail: Optional[str] = None) -> NoReturn:
    raise ApiError(Problem(title=title, status=status, code=code, detail=_short(detail)))


def not_found(what: str, id: str) -> NoReturn:
    problem(404, "not_found", "Not found", f"{what} {id} does not exist.")


def invalid(detail: str) -> NoReturn:
    problem(422, "invalid_request", "The request breaks the contract", detail)


def forbidden(detail: str) -> NoReturn:
    problem(403, "forbidden", "This view may not do this", detail)


def stale(detail: str = "Someone else changed this first. Read it again.") -> NoReturn:
    problem(412, "stale", "Changed since you read it", detail)


def guard(name: str, passed: bool, detail: Optional[str] = None, check_id: Optional[str] = None) -> GuardResult:
    return GuardResult(guard=name, passed=passed, detail=_short(detail), check_id=check_id)


def enforce_guards(guards: Iterable[GuardResult], title: str = "Refused", current_fingerprint: Optional[str] = None) -> None:
    """Raises a 409 listing every failed guard, if any failed. Passing guards are dropped."""
    failed = [g for g in guards if not g.passed]
    if not failed:
        return
    raise ApiError(GuardProblem(
        title=title,
        status=409,
        code="guard_failed",
        detail=f"{len(failed)} guard{'' if len(failed) == 1 else 's'} failed.",
        failed=failed,
        current_fingerprint=current_fingerprint,
    ))


def require_reason(text: Optional[str], what: str = "reason") -> str:
    if not text or not text.strip():
        invalid(f"A {what} is required.")
    return text.strip()


def problem_response(body: Problem) -> JSONResponse:
    return JSONResponse(body.model_dump(mode="json"), status_code=body.status, media_type=MEDIA_TYPE)
