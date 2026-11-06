"""Binds handlers to the operations in the contract's route table."""
import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from pharmalign_contracts.server.routes import ROUTES
from pharmalign_contracts.spec import R

OPERATIONS: dict[tuple[str, str], R] = {(r.method, r.path): r for routes in ROUTES.values() for r in routes}
HANDLERS: dict[tuple[str, str], Callable] = {}


class Params(dict):
    """Path or query values, read as `req.path.case_id`. A query name that is a keyword reads as `req.query["from"]`."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


@dataclass
class Req:
    """A validated request. Query params the caller left out are None."""

    path: Params
    query: Params
    body: Any = None
    if_match: Optional[str] = None


Handler = Callable[[Req, Any], Awaitable[Any]]


@dataclass
class Loose:
    """A reply whose main part is checked against the contract (`head`) and whose `rest` is sent as stored.
    For a draft that is read before it is complete enough to pass its full model."""

    head: Any
    rest: dict


def op(method: str, path: str) -> Callable[[Handler], Handler]:
    """Binds an async handler `(req, ctx)` to one operation. It returns a dict or the contract's model."""

    def bind(fn: Handler) -> Handler:
        key = (method, path)
        if key not in OPERATIONS:
            raise ValueError(f"{method} {path} is not in the contract")
        if key in HANDLERS:
            raise ValueError(f"{method} {path} already has a handler")
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"the handler for {method} {path} must be async")
        HANDLERS[key] = fn
        return fn

    return bind
