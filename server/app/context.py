"""What every handler receives besides its request. Mirrors Ctx in the mock's core.ts."""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from pharmalign_contracts.server.models import Notification, TimelineEvent
from pydantic_core import to_jsonable_python
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from . import audit as audit_log
from .audit import AuditEntry
from .clock import Clock
from .config import Settings
from .identity import Actor, IdentityProvider
from .store import DocStore
from .tables import ROW, DomainEventRow, JobRow

# Which role each view acts under, for the audit log.
ROLE_USED = {
    "platform_admin": "platform_admin",
    "process_owner": "process_owner",
    "case_officer": "case_manager",
    "decision_maker": "decision_maker",
    "auditor": "auditor",
}

TIMELINE = DocStore(DomainEventRow, TimelineEvent, columns=lambda e: {"at": e.at}, fixed={"type": "timeline"})
# The outbox: one event per notification, written with the change that caused it. Handlers never read it.
NOTIFICATIONS = DocStore(DomainEventRow, Notification, columns=lambda n: {"at": n.created_at}, fixed={"type": "notification"})
# What handlers read and write: one delivery per notification, with its state. A worker sends it.
DELIVERIES = DocStore(ROW["notification_delivery"], Notification, columns=lambda n: {
    "event": n.event, "severity": n.severity, "state": n.state, "ack_due_at": n.ack_due_at, "escalation_level": n.escalation_level, "created_at": n.created_at})
ESCALATE_JOB = "notifications.escalate"


@dataclass(frozen=True)
class Tenant:
    id: str
    mode: str  # simulation, shadow or live


@dataclass
class Runtime:
    """One per app: what handlers and workers share."""

    settings: Settings
    clock: Clock
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    identity: IdentityProvider
    handlers: dict
    tasks: dict
    last_tick_at: Optional[datetime] = None
    last_escalations_at: Optional[datetime] = None
    worker: Any = None  # the asyncio task running the job loop, when this process runs one
    agents: Any = None  # an AgentClient
    assistant: Any = None  # an AssistantClient
    fetcher: Any = None  # a WatchFetcher
    connector: Any = None  # a ConnectorAdapter
    background: set = field(default_factory=set)  # asyncio tasks started by a request, held so they aren't collected while they run


@dataclass
class Ctx:
    rt: Runtime
    db: AsyncSession
    actor: Optional[Actor]
    tenant: Optional[Tenant]
    cookies: dict[str, Optional[str]] = field(default_factory=dict)  # set on the response, if it succeeds; None expires the cookie
    _audit: dict[str, list[AuditEntry]] = field(default_factory=dict)  # by workspace; shared by copies made with in_tenant

    @property
    def person(self) -> Optional[Actor]:
        """The signed-in person. None for connectors and background jobs."""
        return self.actor if self.actor and self.actor.type == "person" else None

    @property
    def person_id(self) -> Optional[str]:
        return self.person.id if self.person else None

    @property
    def view(self) -> Optional[str]:
        return self.person.view if self.person else None

    @property
    def workspace_id(self) -> Optional[str]:
        return self.tenant.id if self.tenant else None

    def now(self) -> datetime:
        return self.rt.clock.now()

    def now_ms(self) -> int:
        return int(self.now().timestamp() * 1000)

    def after(self, delay_ms: int) -> datetime:
        """A run_at for schedule, for handlers that think in delays."""
        return self.now() + timedelta(milliseconds=delay_ms)

    def id(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:12]}"

    def audit(self, action: str, object_type: str, object_id: str, case_id: Optional[str] = None, before: Any = None, after: Any = None,
              reason: Optional[str] = None, actor_type: Optional[str] = None, actor_id: Optional[str] = None) -> None:
        """Adds to the audit log when the transaction finishes. Every state change must call this.
        It goes to the chain of this ctx's workspace, so a Platform Admin acting elsewhere calls it on the ctx from in_tenant."""
        self._audit.setdefault(self.tenant.id if self.tenant else "", []).append(AuditEntry(
            at=self.now(),
            actor_type=actor_type or self.actor.type,
            actor_id=actor_id or self.actor.id,
            account_id=self.actor.account_id if self.person and not actor_type else None,
            role_used=ROLE_USED.get(self.view) if self.person and not actor_type else None,
            action=action, object_type=object_type, object_id=object_id, case_id=case_id, before=before, after=after, reason=reason,
        ))

    def timeline(self, case_id: str, kind: str, summary: str, actor: Optional[dict] = None, object_ref: Optional[str] = None) -> None:
        """Adds a case timeline event. `actor` is {"type": ..., "id": ...}, defaulting to the caller."""
        actor_type = actor["type"] if actor else ("person" if self.person else "system")
        actor_id = actor["id"] if actor else self.actor.id
        TIMELINE.add(self, TimelineEvent(
            id=self.id("te"), at=self.now(), kind=kind, summary=summary, actor_type=actor_type, actor_id=actor_id, object_ref=object_ref,
        ), case_id=case_id)

    def notify(self, recipient: str, event: str, severity: str, title: str, body: Optional[str] = None, link: Optional[str] = None,
               ack_within_minutes: Optional[int] = None, id: Optional[str] = None) -> str:
        """Sends an in-app notification to one person. The outbox event and the delivery are written together, with the change that
        caused them; a worker marks the delivery sent. A notification that must be acknowledged schedules its own escalation."""
        ack_due = self.now() + timedelta(minutes=ack_within_minutes) if ack_within_minutes else None
        n = Notification(
            id=id or self.id("nt"), event=event, severity=severity, title=title, body=body, link=link, state="pending", ack_due_at=ack_due, created_at=self.now(),
        )
        NOTIFICATIONS.add(self, n, id=self.id("ev"), recipient=recipient)
        DELIVERIES.add(self, n, recipient=recipient)
        if ack_due:
            self.schedule(ESCALATE_JOB, {"id": n.id}, ack_due)
        return n.id

    def schedule(self, job: str, payload: dict, run_at: Optional[datetime] = None, max_attempts: int = 5) -> str:
        """Queues a background job, committed with this request. Bind it with @task."""
        if job not in self.rt.tasks:
            raise ValueError(f"no task is bound to job {job}")
        job_id = self.id("job")
        self.db.add(JobRow(
            id=job_id, tenant_id=self.tenant.id, kind=job, payload=to_jsonable_python(payload), status="queued", run_at=run_at or self.now(),
            attempts=0, max_attempts=max_attempts, created_at=self.now(),
        ))
        return job_id

    async def flush(self) -> None:
        """Sends everything to the database, audit last. Runs once, just before commit."""
        await self.db.flush()
        for tenant_id in sorted(self._audit):
            await audit_log.write(self.db, tenant_id, self._audit[tenant_id])
        self._audit.clear()
