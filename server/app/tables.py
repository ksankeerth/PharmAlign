"""Database tables. Migrations in migrations/versions create them."""
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Identity, Index, Integer, LargeBinary, SmallInteger, Text, UniqueConstraint, text
from sqlalchemy.types import UserDefinedType
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

TS = DateTime(timezone=True)


class DocRow:
    """Typed columns to query on, plus `doc`: a contract model as JSONB. See store.DocStore."""

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    doc: Mapped[dict] = mapped_column(pg.JSONB, nullable=False)


class PersonRow(Base):
    __tablename__ = "person"
    __table_args__ = (Index("ix_person_tenant_id", "tenant_id"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    view: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[Optional[str]] = mapped_column(Text)
    email: Mapped[Optional[str]] = mapped_column(Text)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False)


class RoleGrantRow(Base):
    __tablename__ = "role_grant"
    __table_args__ = (Index("ix_role_grant_person_id", "person_id"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    person_id: Mapped[str] = mapped_column(Text, ForeignKey("person.id"), nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    discipline: Mapped[Optional[str]] = mapped_column(Text)
    scope: Mapped[Optional[str]] = mapped_column(Text)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[Optional[date]] = mapped_column(Date)


class AuditEventRow(Base):
    """Append-only, enforced by a trigger. Only audit.write adds rows."""

    __tablename__ = "audit_event"
    __table_args__ = (
        UniqueConstraint("tenant_id", "prev_hash", name="uq_audit_event_chain", postgresql_nulls_not_distinct=True),
        Index("ix_audit_event_tenant_case", "tenant_id", "case_id"),
    )

    seq: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(TS, nullable=False)
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(Text, nullable=False)
    account_id: Mapped[Optional[str]] = mapped_column(Text)
    role_used: Mapped[Optional[str]] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    object_type: Mapped[str] = mapped_column(Text, nullable=False)
    object_id: Mapped[str] = mapped_column(Text, nullable=False)
    case_id: Mapped[Optional[str]] = mapped_column(Text)
    # json, not jsonb: values read back exactly as they were hashed.
    before: Mapped[Optional[Any]] = mapped_column(pg.JSON(none_as_null=True))
    after: Mapped[Optional[Any]] = mapped_column(pg.JSON(none_as_null=True))
    reason: Mapped[Optional[str]] = mapped_column(Text)
    prev_hash: Mapped[Optional[str]] = mapped_column(Text)
    row_hash: Mapped[str] = mapped_column(Text, nullable=False)


class DemoStateRow(Base):
    """One row, written by the seed: when the demo data was loaded, and whether a reset is running now."""

    __tablename__ = "demo_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_demo_state_one_row"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    loaded_at: Mapped[Optional[datetime]] = mapped_column(TS)
    with_library: Mapped[bool] = mapped_column(Boolean, nullable=False)
    resetting: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class AuditHeadRow(Base):
    """The last row of each audit chain, set in the same transaction as the append. Verify checks the chain ends there."""

    __tablename__ = "audit_head"

    tenant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    row_id: Mapped[str] = mapped_column(Text, nullable=False)
    row_hash: Mapped[str] = mapped_column(Text, nullable=False)


class DomainEventRow(DocRow, Base):
    """Timeline events and notifications, written with the state change that caused them."""

    __tablename__ = "domain_event"
    __table_args__ = (Index("ix_domain_event_stream", "tenant_id", "type", "case_id"),)

    seq: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    case_id: Mapped[Optional[str]] = mapped_column(Text)
    recipient: Mapped[Optional[str]] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(TS, nullable=False)


class JobRow(Base):
    __tablename__ = "job"
    __table_args__ = (Index("ix_job_due", "status", "run_at"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(pg.JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)  # queued, running, done, failed
    run_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_owner: Mapped[Optional[str]] = mapped_column(Text)
    lease_token: Mapped[Optional[str]] = mapped_column(Text)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(TS)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(TS)


class IdempotencyKeyRow(Base):
    __tablename__ = "idempotency_key"

    tenant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    actor_id: Mapped[str] = mapped_column(Text, primary_key=True)
    operation: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[Optional[int]] = mapped_column(Integer)
    body: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False)


# ---------------------------------------------------------------- the rest of the schema
# Migrations 0002 and 0003 hold the same list. Tests compare the two, so a change goes in both.


class Vector(UserDefinedType):
    """pgvector's `vector`, with no fixed size, so models of different sizes can sit side by side."""

    cache_ok = True

    def get_col_spec(self, **kw):
        return "vector"


ischema_names["vector"] = Vector  # so a schema comparison can read the column back


class GlobalDocRow:
    """A contract model shared by every tenant."""

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    doc: Mapped[dict] = mapped_column(pg.JSONB, nullable=False)


class TenantRow:
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)


class PlainRow:
    id: Mapped[str] = mapped_column(Text, primary_key=True)


DEFAULTS = {None: None, "now": text("now()"), "empty": text("'{}'")}
TYPES = {"t": Text(), "i": Integer(), "b": Boolean(), "ts": TS, "d": Date(), "j": pg.JSONB(), "a": pg.ARRAY(Text()), "v": Vector(), "x": LargeBinary()}
BASES = {"doc": DocRow, "gdoc": GlobalDocRow, "tenant": TenantRow, "plain": PlainRow}

# name, kind, columns, options. Kinds: doc (tenant + doc), gdoc (doc, no tenant), tenant (tenant, no doc), plain (neither).
# A column is name:type, then ! for not null, >table for a foreign key to table.id, =now for a default of now(), =empty for an empty array.
# Types: t text, i integer, b boolean, ts timestamptz, d date, j jsonb, a text[], v vector, x bytea.
SPEC = [
    # Setup
    ("workspace", "doc", "country_code:t! mode:t! active_pack_version_id:t active_kb_snapshot_id:t created_at:ts!", {}),
    ("setup_stage", "doc", "stage:t! status:t!", {"uq": [("tenant_id", "stage")]}),
    # Knowledge. Documents, versions and chunks are shared across countries.
    ("document", "gdoc", "shelf:t! country:t doc_type:t! issuer:t! latest_version_id:t created_at:ts!", {}),
    ("document_version", "gdoc", "document_id:t!>document file_hash:t! review_status:t! quality_status:t published_on:d effective_on:d added_at:ts!", {"ix": [("document_id",), ("file_hash",)]}),
    ("chunk", "gdoc", "document_version_id:t!>document_version position:i! chunk_type:t! page:i page_end:i article:t section_path:t language:t! text:t! text_hash:t!", {"uq": [("document_version_id", "position")]}),
    ("chunk_embedding", "plain", "chunk_id:t!>chunk model:t! dims:i! embedding:v! created_at:ts!=now", {"uq": [("chunk_id", "model")]}),
    ("chunk_terms", "plain", "chunk_id:t!>chunk tokens:t!", {"uq": [("chunk_id",)]}),
    ("tenant_document_adoption", "doc", "document_version_id:t!>document_version adopted:b! legal_status:t! authority_level:i! decided_by:t! decided_at:ts!", {"uq": [("tenant_id", "document_version_id")]}),
    ("kb_snapshot", "doc", "content_hash:t! label:t! item_count:i! created_by:t! created_at:ts!", {}),
    ("kb_snapshot_item", "doc", "snapshot_id:t!>kb_snapshot document_version_id:t!>document_version legal_status:t! authority_level:i!", {"uq": [("snapshot_id", "document_version_id")], "ix": [("document_version_id",)]}),
    ("document_slot", "doc", "key:t! status:t! document_id:t", {"uq": [("tenant_id", "key")]}),
    ("ingest_job", "doc", "document_version_id:t!>document_version status:t! started_at:ts", {}),
    # The file as stored, fixed once written. A version made from the demo data has none.
    ("document_file", "plain", "document_version_id:t!>document_version data:x! created_at:ts!=now", {"uq": [("document_version_id",)]}),
    # The latest quality result for a version. `held` means a person must look before anything is approved.
    ("quality_report", "plain", "document_version_id:t!>document_version ingest_job_id:t!>ingest_job status:t! held:b! doc:j! sketch:j! created_at:ts!=now", {"uq": [("document_version_id",)]}),
    # What a job has parsed so far, so a restart carries on after the slow step instead of starting again.
    ("ingest_output", "plain", "ingest_job_id:t!>ingest_job document_version_id:t!>document_version stage:t! raw:j! chunks:j created_at:ts!=now", {"uq": [("ingest_job_id",)]}),
    ("document_review", "doc", "document_version_id:t!>document_version decision:t! reviewed_by:t! reviewed_at:ts!", {}),
    ("retrieval_test_run", "doc", "status:t!", {}),
    # Packs and setup sign-off
    ("pack_version", "doc", "version:i! state:t! content_hash:t! based_on_version_id:t created_by:t! created_at:ts! approved_by:t approved_at:ts",
     {"uq": [("tenant_id", "version")], "check": [("state", "state IN ('draft','simulated','approved','active','retired')")]}),
    ("readiness_run", "doc", "status:t! started_at:ts!", {}),
    ("sign_off", "doc", "readiness_run_id:t!>readiness_run pack_version_id:t!>pack_version kb_snapshot_id:t!>kb_snapshot target_mode:t! signed_by:t! signed_at:ts!", {}),
    # Medicines
    ("party", "doc", "kind:t! gov_ref:t", {}),
    ("medicine", "doc", "name:t! product_type:t!", {}),
    ("licence", "doc", "medicine_id:t!>medicine holder_party_id:t!>party gov_ref:t gov_status:t! gov_version:t gov_as_of:ts! valid_to:d", {"ix": [("gov_status",)]}),
    ("reliance_basis", "doc", "target_type:t! target_id:t! regulator:t! verified:b!",
     {"ix": [("target_type", "target_id")], "check": [("target_type", "target_type IN ('case','licence')")]}),
    # Identity
    ("delegation", "doc", "from_person_id:t!>person to_person_id:t!>person role:t! valid_from:d! valid_to:d!", {}),
    # Cases
    ("case", "doc", "status:t! environment:t! case_type:t! route:t medicine_id:t!>medicine applicant_party_id:t!>party assigned_to:t pack_version_id:t!>pack_version kb_snapshot_id:t!>kb_snapshot "
     "current_pipeline_run_id:t decision_id:t assessors:a!=empty created_at:ts!",
     {"ix": [("tenant_id", "status"), ("pack_version_id",), ("kb_snapshot_id",)], "check": [("environment", "environment IN ('simulation','live')")]}),
    ("conflict", "doc", "person_id:t!>person party_id:t>party case_id:t>case declared_at:ts!", {}),
    ("case_sequence", "doc", "case_id:t!>case number:i! received_at:ts!", {"uq": [("case_id", "number")]}),
    ("dossier_document", "doc", "case_id:t!>case sequence:i! filename:t! module:t language:t! pages:i", {"ix": [("case_id",)]}),
    ("dossier_page", "doc", "document_id:t!>dossier_document case_id:t!>case page:i! anchor:t! suspect:b!", {"uq": [("document_id", "page")], "ix": [("case_id",)]}),
    ("dossier_chunk", "tenant", "case_id:t!>case document_id:t!>dossier_document position:i! page:i suspect:b! text:t! text_hash:t!", {"ix": [("case_id",)]}),
    ("pipeline_run", "doc", "case_id:t!>case sequence:i! generation:i! status:t! started_at:ts!", {"ix": [("case_id",)]}),
    ("check_result", "doc", "case_id:t!>case check_id:t! pipeline_run_id:t!>pipeline_run stage:t! method:t! status:t! is_current:b! blocking:b! generation:i! attempt:i! reads:a!=empty updated_at:ts!",
     {"uq": [("case_id", "check_id", "generation", "attempt")], "ix": [("case_id", "status"), ("pipeline_run_id",)],
      "partial_uq": [(("case_id", "check_id"), "is_current")]}),
    ("check_trace", "doc", "check_result_id:t!>check_result", {"uq": [("check_result_id",)]}),
    ("agent_run", "doc", "agent:t! case_id:t check_id:t status:t! generation:i attempt:i is_current:b! started_at:ts",
     {"ix": [("case_id", "check_id", "generation", "attempt")],
      "check": [("status", "status IN ('queued','running','completed','stopped_early','failed','cancelled','lost')")]}),
    # What the runner needs to call an agent again. Agents have no grant on it, since it holds the signed run token.
    ("agent_run_work", "doc", "case_id:t!>case posted_at:ts next_poll_at:ts! applied_at:ts", {"ix": [("case_id",)]}),
    ("waiver", "doc", "check_result_id:t!>check_result case_id:t!>case waived_by:t! fingerprint:t! valid:b! at:ts!", {"ix": [("check_result_id",)]}),
    ("confirmation", "doc", "check_result_id:t!>check_result case_id:t!>case confirmed_by:t! fingerprint:t! valid:b! at:ts!", {"ix": [("check_result_id",)]}),
    ("fact", "doc", "case_id:t!>case key:t! status:t!", {"uq": [("case_id", "key")]}),
    ("recommendation_version", "doc", "case_id:t!>case version:i! is_current:b! input_fingerprint:t! created_at:ts!", {"uq": [("case_id", "version")], "partial_uq": [(("case_id",), "is_current")]}),
    ("spot_check", "doc", "case_id:t!>case check_result_id:t!>check_result assigned_to:t status:t!", {}),
    ("review_thread", "doc", "case_id:t!>case status:t! blocks_promotion:b! target_type:t! target_ref:t! created_at:ts!", {"ix": [("case_id", "status")]}),
    ("review_comment", "doc", "thread_id:t!>review_thread case_id:t!>case author_type:t! author_id:t! at:ts!", {"ix": [("thread_id",)]}),
    ("question", "doc", "case_id:t!>case status:t!", {"ix": [("case_id",)]}),
    ("assistant_session", "doc", "case_id:t>case person_id:t! agent_session_id:t kb_snapshot_id:t created_at:ts!", {}),
    ("assistant_exchange", "tenant", "session_id:t!>assistant_session person_id:t! at:ts! message:t! answer:t!", {"ix": [("session_id",)]}),
    ("promotion", "doc", "case_id:t!>case promoted_by:t! fingerprint:t! at:ts!", {"ix": [("case_id",)]}),
    ("reference_page", "tenant", "basis_id:t!>reliance_basis document_id:t! version:t! page:i! text:t!", {"ix": [("basis_id",)]}),
    ("decision", "doc", "case_id:t!>case type:t! state:t! signed_payload_hash:t! recommendation_version:i! legal_effect:t! record_status:t! proposal_id:t foreign_status_reason:t", {"ix": [("case_id",)]}),
    ("decision_signer", "doc", "decision_id:t!>decision case_id:t!>case person_id:t! role:t! signed_at:ts!", {"uq": [("decision_id", "person_id")]}),
    ("proposal", "doc", "decision_id:t!>decision action:t! state:t! idempotency_key:t! created_at:ts! updated_at:ts!",
     {"uq": [("tenant_id", "idempotency_key")], "check": [("state", "state IN ('queued','sent','acknowledged','unknown','rejected')")]}),
    ("connection", "doc", "name:t! kind:t! status:t!", {}),
    # Watch
    ("watch_source", "doc", "regulator:t! kind:t! health:t! last_success_at:ts last_item_at:ts severity_floor:t", {}),
    ("foreign_notice", "doc", "regulator:t! published_on:d fetched_at:ts! latest_revision:i! retracted:b! severity:t! notice_key:t source_id:t reference:t", {"uq": [("tenant_id", "notice_key")]}),
    ("foreign_notice_revision", "doc", "notice_id:t!>foreign_notice revision:i! fetched_at:ts! text_anchor:t! content_hash:t! is_retraction:b! text:t", {"uq": [("notice_id", "revision")]}),
    ("watch_match", "doc", "notice_id:t!>foreign_notice target_type:t! target_id:t status:t! match_confidence:t! rereview_case_id:t",
     {"ix": [("target_type", "target_id")],
      "check": [("target_type", "target_type IN ('licence','case','site','none')"), ("target_none", "(target_type = 'none') = (target_id IS NULL)")]}),
    # Items a staged source holds until a fetch reads them (simulation). Taken once, in the transaction that stores the notice.
    ("watch_staged_item", "tenant", "source_id:t!>watch_source notice:j! staged_at:ts! taken_at:ts", {"ix": [("source_id",)]}),
    ("watch_incident", "doc", "notice_id:t!>foreign_notice status:t! severity:t!", {}),
    # Notifications, gaps, simulation, admin
    ("notification_delivery", "doc", "recipient:t!>person event:t! severity:t! state:t! ack_due_at:ts escalation_level:i! created_at:ts!", {"ix": [("recipient", "state")]}),
    ("gap_signal", "doc", "kind:t!", {}),
    ("gap", "doc", "kind:t! status:t! pack_version_id:t", {}),
    ("gap_analysis", "doc", "status:t! started_at:ts!", {}),
    ("scenario", "doc", "kind:t!", {}),
    ("simulation", "doc", "scenario_id:t!>scenario pack_version_id:t!>pack_version kb_snapshot_id:t!>kb_snapshot status:t! started_at:ts!", {}),
    ("simulation_comparison", "doc", "scenario_id:t!>scenario", {}),
    ("agent_health", "doc", "agent:t! status:t!", {}),
    ("usage_report", "doc", "period_to:ts!", {}),
    # Written by agents, through the one grant they have. Holds hashes and counts, never content.
    ("agent_tool_log", "tenant", "case_id:t run_id:t! tool:t! args_hash:t! result_hash:t outcome:t! hits:i at:ts!=now agent:t result_chars:i ms:i", {"ix": [("case_id", "run_id")]}),
    ("model_registry", "plain", "model:t! role:t! data_class:t! region:t! approved:b! created_at:ts!=now", {"uq": [("model", "role", "data_class")]}),
]


def _column(token: str):
    name, rest = token.split(":")
    default = None
    if "=" in rest:
        rest, default = rest.split("=")
    fk = None
    if ">" in rest:
        rest, fk = rest.split(">")
    args = [TYPES[rest.rstrip("!")]]
    if fk:
        args.append(ForeignKey(f"{fk}.id"))
    return name, mapped_column(*args, nullable=not rest.endswith("!"), server_default=DEFAULTS.get(default))


def row_class(name: str, kind: str, cols: str, opts: dict):
    attrs: dict = dict(_column(t) for t in cols.split())
    args: list = [UniqueConstraint(*c, name=f"uq_{name}_{'_'.join(c)}") for c in opts.get("uq", [])]
    args += [CheckConstraint(sql, name=f"ck_{name}_{label}") for label, sql in opts.get("check", [])]
    if kind in ("doc", "tenant"):
        args.append(Index(f"ix_{name}_tenant_id", "tenant_id"))
    args += [Index(f"ix_{name}_{'_'.join(c)}", *c) for c in opts.get("ix", [])]
    args += [Index(f"uq_{name}_{'_'.join(c)}_{where}", *c, unique=True, postgresql_where=text(where)) for c, where in opts.get("partial_uq", [])]
    attrs.update(__tablename__=name, __table_args__=tuple(args))
    return type("".join(w.title() for w in name.split("_")) + "Row", (BASES[kind], Base), attrs)


ROW = {name: row_class(name, kind, cols, opts) for name, kind, cols, opts in SPEC}


from .mock_regulator import tables as _mock_regulator_tables  # noqa: E402,F401  the mock regulator's own tables


# Vector indexes, one per model, on a cast to that model's size. Migration 0007 creates the same.
for _model, _dims in {"hash-256": 256, "bge-m3": 1024}.items():
    Index(
        f"ix_chunk_embedding_{_model.replace('-', '_')}", text(f"(embedding::vector({_dims})) vector_cosine_ops"),
        _table=ROW["chunk_embedding"].__table__, postgresql_using="hnsw", postgresql_where=text(f"model = '{_model}'"),
    )
