"""Who is calling. A provider turns a request into an Actor. The only provider so far is demo."""
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Optional, Protocol

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .tables import ROW, PersonRow, RoleGrantRow

SESSION_COOKIE = "pharmalign_session"
WORKSPACE_COOKIE = "pharmalign_workspace"


@dataclass(frozen=True)
class Actor:
    type: str  # person, connector or system
    id: str
    tenant_id: str
    display_name: str = ""
    view: Optional[str] = None
    is_demo: bool = False
    roles: tuple[str, ...] = ()
    account_id: Optional[str] = None


class IdentityProvider(Protocol):
    is_demo: bool

    async def authenticate(self, db: AsyncSession, request: Request, now: datetime) -> Optional[Actor]:
        """The person behind this request, or None if nobody is signed in."""

    def session_cookies(self, person_id: str) -> dict[str, str]:
        """Cookies that make later requests come from this person."""


def bearer_token(request: Request) -> Optional[str]:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token.strip() or None if scheme.lower() == "bearer" else None


async def load_actor(db: AsyncSession, person_id: str, now: datetime, account_id: Optional[str] = None) -> Optional[Actor]:
    person = await db.get(PersonRow, person_id)
    if person is None:
        return None
    today = now.date()
    grants = await db.scalars(
        select(RoleGrantRow).where(RoleGrantRow.person_id == person_id, RoleGrantRow.valid_from <= today).order_by(RoleGrantRow.id)
    )
    roles = [g.role for g in grants if g.valid_to is None or g.valid_to >= today]
    return Actor(
        type="person", id=person.id, tenant_id=person.tenant_id, display_name=person.display_name, view=person.view,
        is_demo=person.is_demo, roles=tuple(dict.fromkeys(roles)), account_id=account_id,
    )


class DemoIdentity:
    """Anyone can be any demo person. create_app refuses this outside simulation.

    The session cookie holds the person id, because the UI sends cookies and no token.
    A bearer token holding a person id also works. With neither, you are the default person.
    A Platform Admin can also hold a workspace cookie, which moves them into that workspace.
    """

    is_demo = True

    def __init__(self, default_person: str):
        self.default_person = default_person

    async def authenticate(self, db: AsyncSession, request: Request, now: datetime) -> Optional[Actor]:
        person_id = bearer_token(request) or request.cookies.get(SESSION_COOKIE) or self.default_person
        actor = await load_actor(db, person_id, now, account_id="acct-demo")
        workspace_id = request.cookies.get(WORKSPACE_COOKIE)
        # Only the Platform Admin view is honoured, the same rule as reach(). For anyone else the cookie is ignored.
        if actor and actor.view == "platform_admin" and workspace_id and await db.scalar(select(ROW["workspace"].id).where(ROW["workspace"].id == workspace_id)):
            return replace(actor, tenant_id=workspace_id)
        return actor

    def session_cookies(self, person_id: str) -> dict[str, str]:
        return {SESSION_COOKIE: person_id}
