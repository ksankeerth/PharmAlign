"""Every table the mock state holds, the rules that keep records fixed, and the agent perimeter.

Tables follow one pattern: typed columns for anything a guard, filter or join needs, `tenant_id` on tenant rows, and
`doc`, the contract model as JSONB. The agent role reads only the `agent_read` views and writes only `agent_tool_log`.
"""
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


class Vector(sa.types.UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw):
        return "vector"


DEFAULTS = {None: None, "now": sa.text("now()"), "empty": sa.text("'{}'")}
TYPES = {"t": sa.Text(), "i": sa.Integer(), "b": sa.Boolean(), "ts": TS, "d": sa.Date(), "j": pg.JSONB(), "a": pg.ARRAY(sa.Text()), "v": Vector()}

# name, kind, columns, options. Kinds: doc (tenant + doc), gdoc (doc, no tenant), tenant (tenant, no doc), plain (neither).
# A column is name:type, then ! for not null, >table for a foreign key to table.id, =now for a default of now(), =empty for an empty array.
# Types: t text, i integer, b boolean, ts timestamptz, d date, j jsonb, a text[], v vector.
SPEC = [
    # Setup
    ("workspace", "doc", "country_code:t! mode:t! active_pack_version_id:t active_kb_snapshot_id:t created_at:ts!", {}),
    ("setup_stage", "doc", "stage:t! status:t!", {"uq": [("tenant_id", "stage")]}),
    # Knowledge. Documents, versions and chunks are shared across countries.
    ("document", "gdoc", "shelf:t! country:t doc_type:t! issuer:t! latest_version_id:t created_at:ts!", {}),
    ("document_version", "gdoc", "document_id:t!>document file_hash:t! review_status:t! quality_status:t published_on:d effective_on:d added_at:ts!", {"ix": [("document_id",), ("file_hash",)]}),
    ("chunk", "gdoc", "document_version_id:t!>document_version position:i! chunk_type:t! page:i page_end:i article:t section_path:t language:t! text:t! text_hash:t!", {"uq": [("document_version_id", "position")]}),
    ("chunk_embedding", "plain", "chunk_id:t!>chunk model:t! dims:i! embedding:v! created_at:ts!=now", {"uq": [("chunk_id", "model")]}),
    ("tenant_document_adoption", "doc", "document_version_id:t!>document_version adopted:b! legal_status:t! authority_level:i! decided_by:t! decided_at:ts!", {"uq": [("tenant_id", "document_version_id")]}),
    ("kb_snapshot", "doc", "content_hash:t! label:t! item_count:i! created_by:t! created_at:ts!", {}),
    ("kb_snapshot_item", "doc", "snapshot_id:t!>kb_snapshot document_version_id:t!>document_version legal_status:t! authority_level:i!", {"uq": [("snapshot_id", "document_version_id")], "ix": [("document_version_id",)]}),
    ("document_slot", "doc", "key:t! status:t! document_id:t", {"uq": [("tenant_id", "key")]}),
    ("ingest_job", "doc", "document_version_id:t!>document_version status:t! started_at:ts", {}),
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
    ("waiver", "doc", "check_result_id:t!>check_result case_id:t!>case waived_by:t! fingerprint:t! valid:b! at:ts!", {"ix": [("check_result_id",)]}),
    ("confirmation", "doc", "check_result_id:t!>check_result case_id:t!>case confirmed_by:t! fingerprint:t! valid:b! at:ts!", {"ix": [("check_result_id",)]}),
    ("fact", "doc", "case_id:t!>case key:t! status:t!", {"uq": [("case_id", "key")]}),
    ("recommendation_version", "doc", "case_id:t!>case version:i! is_current:b! input_fingerprint:t! created_at:ts!", {"uq": [("case_id", "version")], "partial_uq": [(("case_id",), "is_current")]}),
    ("spot_check", "doc", "case_id:t!>case check_result_id:t!>check_result assigned_to:t status:t!", {}),
    ("review_thread", "doc", "case_id:t!>case status:t! blocks_promotion:b! target_type:t! target_ref:t! created_at:ts!", {"ix": [("case_id", "status")]}),
    ("review_comment", "doc", "thread_id:t!>review_thread case_id:t!>case author_type:t! author_id:t! at:ts!", {"ix": [("thread_id",)]}),
    ("question", "doc", "case_id:t!>case status:t!", {"ix": [("case_id",)]}),
    ("assistant_session", "doc", "case_id:t!>case person_id:t! created_at:ts!", {}),
    ("assistant_exchange", "tenant", "session_id:t!>assistant_session person_id:t! at:ts! message:t! answer:t!", {"ix": [("session_id",)]}),
    ("promotion", "doc", "case_id:t!>case promoted_by:t! fingerprint:t! at:ts!", {"ix": [("case_id",)]}),
    ("decision", "doc", "case_id:t!>case type:t! state:t! signed_payload_hash:t! recommendation_version:i! legal_effect:t! record_status:t! proposal_id:t", {"ix": [("case_id",)]}),
    ("decision_signer", "doc", "decision_id:t!>decision case_id:t!>case person_id:t! role:t! signed_at:ts!", {"uq": [("decision_id", "person_id")]}),
    ("proposal", "doc", "decision_id:t!>decision action:t! state:t! idempotency_key:t! created_at:ts! updated_at:ts!",
     {"uq": [("tenant_id", "idempotency_key")], "check": [("state", "state IN ('queued','sent','acknowledged','unknown','rejected')")]}),
    ("connection", "doc", "name:t! kind:t! status:t!", {}),
    # Watch
    ("watch_source", "doc", "regulator:t! kind:t! health:t! last_success_at:ts", {}),
    ("foreign_notice", "doc", "regulator:t! published_on:d fetched_at:ts! latest_revision:i! retracted:b! severity:t!", {}),
    ("foreign_notice_revision", "doc", "notice_id:t!>foreign_notice revision:i! fetched_at:ts! text_anchor:t! content_hash:t! is_retraction:b!", {"uq": [("notice_id", "revision")]}),
    ("watch_match", "doc", "notice_id:t!>foreign_notice target_type:t! target_id:t status:t! match_confidence:t!",
     {"ix": [("target_type", "target_id")],
      "check": [("target_type", "target_type IN ('licence','case','site','none')"), ("target_none", "(target_type = 'none') = (target_id IS NULL)")]}),
    ("watch_incident", "doc", "notice_id:t!>foreign_notice status:t! severity:t!", {}),
    # Notifications, gaps, simulation, admin
    ("notification_delivery", "doc", "recipient:t!>person event:t! severity:t! state:t! ack_due_at:ts escalation_level:i! created_at:ts!", {"ix": [("recipient", "state")]}),
    ("gap_signal", "doc", "kind:t!", {}),
    ("gap", "doc", "kind:t! status:t!", {}),
    ("gap_analysis", "doc", "status:t! started_at:ts!", {}),
    ("scenario", "doc", "kind:t!", {}),
    ("simulation", "doc", "scenario_id:t!>scenario pack_version_id:t!>pack_version kb_snapshot_id:t!>kb_snapshot status:t! started_at:ts!", {}),
    ("simulation_comparison", "doc", "scenario_id:t!>scenario", {}),
    ("agent_health", "doc", "agent:t! status:t!", {}),
    ("usage_report", "doc", "period_to:ts!", {}),
    # Written by agents, through the one grant they have. Holds hashes and counts, never content.
    ("agent_tool_log", "tenant", "case_id:t run_id:t! tool:t! args_hash:t! result_hash:t outcome:t! hits:i at:ts!=now", {"ix": [("case_id", "run_id")]}),
    ("model_registry", "plain", "model:t! role:t! data_class:t! region:t! approved:b! created_at:ts!=now", {"uq": [("model", "role", "data_class")]}),
]


def column(token: str) -> sa.Column:
    name, rest = token.split(":")
    default = None
    if "=" in rest:
        rest, default = rest.split("=")
    fk = None
    if ">" in rest:
        rest, fk = rest.split(">")
    args = [TYPES[rest.rstrip("!")]]
    if fk:
        args.append(sa.ForeignKey(f"{fk}.id"))
    return sa.Column(name, *args, nullable=not rest.endswith("!"), server_default=DEFAULTS.get(default))


def create(name: str, kind: str, cols: str, opts: dict) -> None:
    columns = [sa.Column("id", sa.Text(), primary_key=True)]
    if kind in ("doc", "tenant"):
        columns.append(sa.Column("tenant_id", sa.Text(), nullable=False))
    columns += [column(t) for t in cols.split()]
    if kind in ("doc", "gdoc"):
        columns.append(sa.Column("doc", pg.JSONB(), nullable=False))
    extra = [sa.UniqueConstraint(*c, name=f"uq_{name}_{'_'.join(c)}") for c in opts.get("uq", [])]
    extra += [sa.CheckConstraint(sql, name=f"ck_{name}_{label}") for label, sql in opts.get("check", [])]
    op.create_table(name, *columns, *extra)
    if kind in ("doc", "tenant"):
        op.create_index(f"ix_{name}_tenant_id", name, ["tenant_id"])
    for c in opts.get("ix", []):
        op.create_index(f"ix_{name}_{'_'.join(c)}", name, list(c))
    for c, where in opts.get("partial_uq", []):
        op.create_index(f"uq_{name}_{'_'.join(c)}_{where}", name, list(c), unique=True, postgresql_where=sa.text(where))


FUNCTIONS = """
CREATE FUNCTION refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is fixed once written', TG_TABLE_NAME USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

CREATE FUNCTION pack_version_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.approved_at IS NOT NULL THEN
            RAISE EXCEPTION 'an approved pack version cannot be deleted' USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.approved_at IS NOT NULL AND (
        NEW.id <> OLD.id OR NEW.tenant_id <> OLD.tenant_id OR NEW.version <> OLD.version OR NEW.content_hash <> OLD.content_hash
        OR NEW.approved_at IS DISTINCT FROM OLD.approved_at OR NEW.approved_by IS DISTINCT FROM OLD.approved_by
        OR NEW.based_on_version_id IS DISTINCT FROM OLD.based_on_version_id OR (NEW.doc - 'state') <> (OLD.doc - 'state')
    ) THEN
        RAISE EXCEPTION 'an approved pack version cannot change' USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION reliance_basis_target() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.target_type = 'case' AND NOT EXISTS (SELECT 1 FROM "case" WHERE id = NEW.target_id AND tenant_id = NEW.tenant_id)
       OR NEW.target_type = 'licence' AND NOT EXISTS (SELECT 1 FROM licence WHERE id = NEW.target_id AND tenant_id = NEW.tenant_id) THEN
        RAISE EXCEPTION 'reliance basis target % % does not exist', NEW.target_type, NEW.target_id USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$;
"""

TRIGGERS = """
CREATE TRIGGER chunk_no_update BEFORE UPDATE ON chunk FOR EACH ROW
    WHEN (OLD.id IS DISTINCT FROM NEW.id OR OLD.document_version_id IS DISTINCT FROM NEW.document_version_id OR OLD."position" IS DISTINCT FROM NEW."position"
          OR OLD.text IS DISTINCT FROM NEW.text OR OLD.text_hash IS DISTINCT FROM NEW.text_hash OR OLD.doc IS DISTINCT FROM NEW.doc)
    EXECUTE FUNCTION refuse_change();
CREATE TRIGGER chunk_no_delete BEFORE DELETE ON chunk FOR EACH ROW EXECUTE FUNCTION refuse_change();
CREATE TRIGGER chunk_no_truncate BEFORE TRUNCATE ON chunk FOR EACH STATEMENT EXECUTE FUNCTION refuse_change();

CREATE TRIGGER kb_snapshot_item_fixed BEFORE UPDATE OR DELETE ON kb_snapshot_item FOR EACH ROW EXECUTE FUNCTION refuse_change();
CREATE TRIGGER kb_snapshot_item_no_truncate BEFORE TRUNCATE ON kb_snapshot_item FOR EACH STATEMENT EXECUTE FUNCTION refuse_change();

CREATE TRIGGER dossier_chunk_no_update BEFORE UPDATE ON dossier_chunk FOR EACH ROW
    WHEN (OLD.text IS DISTINCT FROM NEW.text OR OLD.text_hash IS DISTINCT FROM NEW.text_hash OR OLD.case_id IS DISTINCT FROM NEW.case_id)
    EXECUTE FUNCTION refuse_change();

CREATE TRIGGER pack_version_fixed BEFORE UPDATE OR DELETE ON pack_version FOR EACH ROW EXECUTE FUNCTION pack_version_guard();
ALTER TABLE pack_version ADD CONSTRAINT ck_pack_version_approval CHECK ((approved_at IS NULL) = (state IN ('draft', 'simulated')));

CREATE TRIGGER reliance_basis_target_exists BEFORE INSERT OR UPDATE ON reliance_basis FOR EACH ROW EXECUTE FUNCTION reliance_basis_target();
"""

# What an agent may read. A view shows only the tenant and case the server set for the run with
#   SELECT set_config('app.tenant_id', ..., true), set_config('app.case_id', ..., true)
# Unset means nothing is visible. The views run with their owner's rights, so the role needs no table grants.
VIEWS = """
CREATE SCHEMA agent_read;
CREATE FUNCTION agent_read.run_tenant() RETURNS text LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.tenant_id', true), '') $$;
CREATE FUNCTION agent_read.run_case() RETURNS text LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.case_id', true), '') $$;

CREATE VIEW agent_read.case_context AS
SELECT c.id AS case_id, c.tenant_id, c.case_type, c.route, c.environment, c.pack_version_id, c.kb_snapshot_id,
       m.name AS medicine_name, m.doc -> 'substances' AS substances, m.doc ->> 'strength' AS strength, m.doc ->> 'form' AS form, m.product_type
FROM "case" c JOIN medicine m ON m.id = c.medicine_id
WHERE c.tenant_id = agent_read.run_tenant() AND c.id = agent_read.run_case();

-- Chunks of the run's pinned snapshot. Only reviewed documents that passed the quality gate.
CREATE VIEW agent_read.kb_chunk AS
SELECT ch.id AS anchor, ch.document_version_id, dv.document_id, d.doc ->> 'title' AS title, d.issuer, d.country,
       i.legal_status, i.authority_level, (i.legal_status = 'in_force') AS usable_as_requirement,
       ch."position", ch.chunk_type, ch.page, ch.page_end, ch.article, ch.section_path, ch.language, ch.text, ch.text_hash
FROM "case" c
JOIN kb_snapshot_item i ON i.snapshot_id = c.kb_snapshot_id AND i.tenant_id = c.tenant_id
JOIN document_version dv ON dv.id = i.document_version_id
JOIN document d ON d.id = dv.document_id
JOIN chunk ch ON ch.document_version_id = dv.id
WHERE c.tenant_id = agent_read.run_tenant() AND c.id = agent_read.run_case()
  AND dv.review_status IN ('approved', 'approved_with_notes') AND coalesce(dv.quality_status, 'attention') <> 'fail';

-- What a "required by" claim may cite: in-force sources only.
CREATE VIEW agent_read.kb_requirement_chunk AS SELECT * FROM agent_read.kb_chunk WHERE usable_as_requirement;

CREATE VIEW agent_read.dossier_document AS
SELECT id AS document_id, case_id, sequence, filename, module, language, pages
FROM dossier_document WHERE tenant_id = agent_read.run_tenant() AND case_id = agent_read.run_case();

-- Visible text only. Hidden text stays out.
CREATE VIEW agent_read.dossier_page AS
SELECT id, document_id, case_id, page, anchor, doc ->> 'text' AS text, suspect
FROM dossier_page WHERE tenant_id = agent_read.run_tenant() AND case_id = agent_read.run_case();

CREATE VIEW agent_read.dossier_chunk AS
SELECT id AS anchor, case_id, document_id, "position", page, suspect, text, text_hash
FROM dossier_chunk WHERE tenant_id = agent_read.run_tenant() AND case_id = agent_read.run_case();

CREATE VIEW agent_read.case_fact AS
SELECT case_id, key, status, doc -> 'evidence' AS evidence, doc ->> 'value' AS value
FROM fact WHERE tenant_id = agent_read.run_tenant() AND case_id = agent_read.run_case();

CREATE VIEW agent_read.reliance AS
SELECT id, regulator, doc ->> 'approval_ref' AS approval_ref, doc ->> 'approved_on' AS approved_on, verified
FROM reliance_basis WHERE tenant_id = agent_read.run_tenant() AND target_type = 'case' AND target_id = agent_read.run_case();
"""

PERIMETER = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pharmalign_agent') THEN
        -- Development password. Set a real one outside migrations.
        CREATE ROLE pharmalign_agent LOGIN PASSWORD 'pharmalign_agent' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO pharmalign_agent', current_database());
END;
$$;
GRANT USAGE ON SCHEMA agent_read TO pharmalign_agent;
GRANT SELECT ON ALL TABLES IN SCHEMA agent_read TO pharmalign_agent;
GRANT INSERT ON agent_tool_log TO pharmalign_agent;
ALTER TABLE agent_tool_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_tool_log_insert ON agent_tool_log FOR INSERT TO pharmalign_agent
    WITH CHECK (tenant_id = agent_read.run_tenant() AND case_id IS NOT DISTINCT FROM agent_read.run_case());
"""


def run(script: str) -> None:
    """One statement at a time, since the driver takes one. A statement starts with a keyword at the start of a line, after a semicolon."""
    for statement in re.split(r";[ ]*\n+(?=--|CREATE|ALTER|GRANT|DO )", script.strip()):
        if statement.strip():
            op.execute(statement.strip().removesuffix(";"))


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    for name, kind, cols, opts in SPEC:
        create(name, kind, cols, opts)
    run(FUNCTIONS)
    run(TRIGGERS)
    run(VIEWS)
    run(PERIMETER)


def downgrade() -> None:
    op.execute("DROP OWNED BY pharmalign_agent")
    op.execute("DROP SCHEMA agent_read CASCADE")
    for name, *_ in reversed(SPEC):
        op.drop_table(name)
    for fn in ("refuse_change", "pack_version_guard", "reliance_basis_target"):
        op.execute(f"DROP FUNCTION {fn}()")
