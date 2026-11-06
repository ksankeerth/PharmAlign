"""What the agent tool server reads and logs.

A knowledge-only session has no case, so the knowledge views take their snapshot from `app.snapshot_id` when no case is set,
and from the case's pinned snapshot when one is. New views cover the case's checks, traces, runs, recommendations, pack,
notices and applicant, and tenant-level notices, licences, reliance bases, gap signals and pack rules for runs with no case. The tool log gains the agent, the result size and the time taken.
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

SCOPED_SNAPSHOT = """
CASE WHEN agent_read.run_case() IS NULL THEN agent_read.run_snapshot()
     ELSE (SELECT c.kb_snapshot_id FROM "case" c WHERE c.tenant_id = agent_read.run_tenant() AND c.id = agent_read.run_case()) END
"""

UPGRADE = [
    "ALTER TABLE agent_tool_log ADD COLUMN agent text, ADD COLUMN result_chars integer, ADD COLUMN ms integer",
    "CREATE FUNCTION agent_read.run_snapshot() RETURNS text LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.snapshot_id', true), '') $$",
    # Same columns as before, so the views built on it stay.
    f"""CREATE OR REPLACE VIEW agent_read.kb_chunk AS
SELECT ch.id AS anchor, ch.document_version_id, dv.document_id, d.doc ->> 'title' AS title, d.issuer, d.country,
       i.legal_status, i.authority_level, (i.legal_status = 'in_force') AS usable_as_requirement,
       ch."position", ch.chunk_type, ch.page, ch.page_end, ch.article, ch.section_path, ch.language, ch.text, ch.text_hash
FROM kb_snapshot_item i
JOIN document_version dv ON dv.id = i.document_version_id
JOIN document d ON d.id = dv.document_id
JOIN chunk ch ON ch.document_version_id = dv.id
WHERE i.tenant_id = agent_read.run_tenant() AND i.snapshot_id = ({SCOPED_SNAPSHOT})
  AND dv.review_status IN ('approved', 'approved_with_notes') AND coalesce(dv.quality_status, 'attention') <> 'fail'""",
    # Every version in the snapshot, usable or not, so a tool can say why one can't be cited.
    f"""CREATE VIEW agent_read.kb_document AS
SELECT i.snapshot_id, dv.document_id, dv.id AS document_version_id, d.doc ->> 'title' AS title, d.issuer, d.country, d.doc_type,
       i.legal_status, i.authority_level, dv.review_status, dv.quality_status, dv.published_on, dv.effective_on,
       (dv.review_status IN ('approved', 'approved_with_notes') AND coalesce(dv.quality_status, 'attention') <> 'fail') AS readable
FROM kb_snapshot_item i
JOIN document_version dv ON dv.id = i.document_version_id
JOIN document d ON d.id = dv.document_id
WHERE i.tenant_id = agent_read.run_tenant() AND i.snapshot_id = ({SCOPED_SNAPSHOT})""",
    """CREATE OR REPLACE VIEW agent_read.case_context AS
SELECT c.id AS case_id, c.tenant_id, c.case_type, c.route, c.environment, c.pack_version_id, c.kb_snapshot_id,
       m.name AS medicine_name, m.doc -> 'substances' AS substances, m.doc ->> 'strength' AS strength, m.doc ->> 'form' AS form, m.product_type,
       (c.doc ->> 'sequence')::int AS sequence, c.current_pipeline_run_id, c.applicant_party_id
FROM "case" c JOIN medicine m ON m.id = c.medicine_id
WHERE c.tenant_id = agent_read.run_tenant() AND c.id = agent_read.run_case()""",
    """CREATE OR REPLACE VIEW agent_read.dossier_document AS
SELECT id AS document_id, case_id, sequence, filename, module, language, pages, doc ->> 'version' AS version, doc -> 'suspect_pages' AS suspect_pages
FROM dossier_document WHERE tenant_id = agent_read.run_tenant() AND case_id = agent_read.run_case()""",
    """CREATE OR REPLACE VIEW agent_read.reliance AS
SELECT id, regulator, doc ->> 'approval_ref' AS approval_ref, doc ->> 'approved_on' AS approved_on, verified,
       doc -> 'document_refs' AS document_refs, doc ->> 'declared_same_by' AS declared_same_by
FROM reliance_basis WHERE tenant_id = agent_read.run_tenant() AND target_type = 'case' AND target_id = agent_read.run_case()""",
    """CREATE VIEW agent_read.case_check AS
SELECT id AS result_id, case_id, check_id, pipeline_run_id, stage, method, status, is_current, blocking, generation, attempt, updated_at, doc AS detail
FROM check_result WHERE tenant_id = agent_read.run_tenant() AND case_id = agent_read.run_case()""",
    """CREATE VIEW agent_read.case_trace AS
SELECT t.check_result_id, r.check_id, r.is_current, t.doc AS trace
FROM check_trace t JOIN check_result r ON r.id = t.check_result_id AND r.tenant_id = t.tenant_id
WHERE t.tenant_id = agent_read.run_tenant() AND r.case_id = agent_read.run_case()""",
    """CREATE VIEW agent_read.case_pipeline_run AS
SELECT id AS pipeline_run_id, case_id, sequence, generation, status, started_at, doc ->> 'finished_at' AS finished_at, doc -> 'stages' AS stages
FROM pipeline_run WHERE tenant_id = agent_read.run_tenant() AND case_id = agent_read.run_case()""",
    """CREATE VIEW agent_read.case_recommendation AS
SELECT case_id, version, is_current, input_fingerprint, created_at, doc ->> 'choice' AS choice, doc ->> 'reason' AS reason, doc ->> 'summary' AS summary
FROM recommendation_version WHERE tenant_id = agent_read.run_tenant() AND case_id = agent_read.run_case()""",
    # The pinned pack's checks, and the workspace's active pack's, for a comparison.
    """CREATE VIEW agent_read.case_pack AS
SELECT c.id AS case_id, p.id AS pinned_id, p.version AS pinned_version, p.doc -> 'document' -> 'checks' AS pinned_checks,
       a.id AS active_id, a.version AS active_version, a.doc -> 'document' -> 'checks' AS active_checks
FROM "case" c
JOIN pack_version p ON p.id = c.pack_version_id AND p.tenant_id = c.tenant_id
LEFT JOIN workspace w ON w.id = c.tenant_id AND w.tenant_id = c.tenant_id
LEFT JOIN pack_version a ON a.id = w.active_pack_version_id AND a.tenant_id = c.tenant_id
WHERE c.tenant_id = agent_read.run_tenant() AND c.id = agent_read.run_case()""",
    # Foreign notices matched to this case, at their latest revision.
    """CREATE VIEW agent_read.case_notice AS
SELECT n.id AS notice_id, n.regulator, n.published_on, n.retracted, n.severity, n.reference, r.revision, r.text_anchor, r.doc ->> 'text' AS text,
       m.status AS match_status, m.match_confidence, m.doc -> 'match_basis' AS match_basis
FROM watch_match m
JOIN foreign_notice n ON n.id = m.notice_id AND n.tenant_id = m.tenant_id
JOIN foreign_notice_revision r ON r.notice_id = n.id AND r.revision = n.latest_revision AND r.tenant_id = n.tenant_id
WHERE m.tenant_id = agent_read.run_tenant() AND m.target_type = 'case' AND m.target_id = agent_read.run_case()""",
    """CREATE VIEW agent_read.case_applicant AS
SELECT p.id AS party_id, p.doc ->> 'name' AS name, p.doc ->> 'kind' AS kind, p.doc ->> 'country' AS country, p.doc ->> 'gov_ref' AS gov_ref
FROM "case" c JOIN party p ON p.id = c.applicant_party_id AND p.tenant_id = c.tenant_id
WHERE c.tenant_id = agent_read.run_tenant() AND c.id = agent_read.run_case()""",
    # Tenant-level reads for runs with no case (Reliance Watch, Gap Analyst). With a case set, they narrow to that case.
    """CREATE VIEW agent_read.notice AS
SELECT n.id AS notice_id, n.regulator, n.reference, n.doc ->> 'title' AS title, n.doc ->> 'language' AS language, n.published_on, n.fetched_at,
       n.retracted, n.severity, r.revision, r.text_anchor, r.doc ->> 'text' AS text, r.is_retraction
FROM foreign_notice n JOIN foreign_notice_revision r ON r.notice_id = n.id AND r.revision = n.latest_revision AND r.tenant_id = n.tenant_id
WHERE n.tenant_id = agent_read.run_tenant()""",
    """CREATE VIEW agent_read.licence AS
SELECT l.id AS licence_id, l.medicine_id, md.name AS medicine_name, md.doc -> 'substances' AS substances, md.doc ->> 'strength' AS strength,
       md.doc ->> 'form' AS form, l.holder_party_id, p.doc ->> 'name' AS holder_name, l.gov_ref, l.gov_status, l.gov_version, l.gov_as_of, l.valid_to
FROM licence l
JOIN medicine md ON md.id = l.medicine_id AND md.tenant_id = l.tenant_id
LEFT JOIN party p ON p.id = l.holder_party_id AND p.tenant_id = l.tenant_id
WHERE l.tenant_id = agent_read.run_tenant()
  AND (agent_read.run_case() IS NULL
       OR l.medicine_id = (SELECT c.medicine_id FROM "case" c WHERE c.tenant_id = agent_read.run_tenant() AND c.id = agent_read.run_case()))""",
    """CREATE VIEW agent_read.relied_on AS
SELECT id, target_type, target_id, regulator, doc ->> 'approval_ref' AS approval_ref, doc ->> 'approved_on' AS approved_on, verified,
       doc -> 'document_refs' AS document_refs
FROM reliance_basis WHERE tenant_id = agent_read.run_tenant()
  AND (agent_read.run_case() IS NULL OR (target_type = 'case' AND target_id = agent_read.run_case()))""",
    """CREATE VIEW agent_read.gap_signal AS
SELECT id, kind, doc ->> 'detail' AS detail, coalesce((doc ->> 'count')::int, 1) AS count FROM gap_signal WHERE tenant_id = agent_read.run_tenant()""",
    # The case's pinned pack, or the workspace's active pack when there is no case.
    """CREATE VIEW agent_read.pack_rule AS
SELECT p.id AS pack_version_id, p.version, r.value ->> 'id' AS check_id, r.value AS rule
FROM pack_version p CROSS JOIN LATERAL jsonb_array_elements(p.doc -> 'document' -> 'checks') r
WHERE p.tenant_id = agent_read.run_tenant() AND p.id = CASE WHEN agent_read.run_case() IS NULL
    THEN (SELECT w.active_pack_version_id FROM workspace w WHERE w.id = agent_read.run_tenant() AND w.tenant_id = agent_read.run_tenant())
    ELSE (SELECT c.pack_version_id FROM "case" c WHERE c.tenant_id = agent_read.run_tenant() AND c.id = agent_read.run_case()) END""",
    "GRANT SELECT ON ALL TABLES IN SCHEMA agent_read TO pharmalign_agent",
]

OLD_VIEWS = [
    """CREATE OR REPLACE VIEW agent_read.kb_chunk AS
SELECT ch.id AS anchor, ch.document_version_id, dv.document_id, d.doc ->> 'title' AS title, d.issuer, d.country,
       i.legal_status, i.authority_level, (i.legal_status = 'in_force') AS usable_as_requirement,
       ch."position", ch.chunk_type, ch.page, ch.page_end, ch.article, ch.section_path, ch.language, ch.text, ch.text_hash
FROM "case" c
JOIN kb_snapshot_item i ON i.snapshot_id = c.kb_snapshot_id AND i.tenant_id = c.tenant_id
JOIN document_version dv ON dv.id = i.document_version_id
JOIN document d ON d.id = dv.document_id
JOIN chunk ch ON ch.document_version_id = dv.id
WHERE c.tenant_id = agent_read.run_tenant() AND c.id = agent_read.run_case()
  AND dv.review_status IN ('approved', 'approved_with_notes') AND coalesce(dv.quality_status, 'attention') <> 'fail'""",
    """CREATE VIEW agent_read.case_context AS
SELECT c.id AS case_id, c.tenant_id, c.case_type, c.route, c.environment, c.pack_version_id, c.kb_snapshot_id,
       m.name AS medicine_name, m.doc -> 'substances' AS substances, m.doc ->> 'strength' AS strength, m.doc ->> 'form' AS form, m.product_type
FROM "case" c JOIN medicine m ON m.id = c.medicine_id
WHERE c.tenant_id = agent_read.run_tenant() AND c.id = agent_read.run_case()""",
    """CREATE VIEW agent_read.dossier_document AS
SELECT id AS document_id, case_id, sequence, filename, module, language, pages
FROM dossier_document WHERE tenant_id = agent_read.run_tenant() AND case_id = agent_read.run_case()""",
    """CREATE VIEW agent_read.reliance AS
SELECT id, regulator, doc ->> 'approval_ref' AS approval_ref, doc ->> 'approved_on' AS approved_on, verified
FROM reliance_basis WHERE tenant_id = agent_read.run_tenant() AND target_type = 'case' AND target_id = agent_read.run_case()""",
    "GRANT SELECT ON ALL TABLES IN SCHEMA agent_read TO pharmalign_agent",
]

NEW_VIEWS = ("kb_document", "case_check", "case_trace", "case_pipeline_run", "case_recommendation", "case_pack", "case_notice", "case_applicant",
             "notice", "licence", "relied_on", "gap_signal", "pack_rule")


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for view in NEW_VIEWS:
        op.execute(f"DROP VIEW agent_read.{view}")
    op.execute("DROP VIEW agent_read.case_context")
    op.execute("DROP VIEW agent_read.dossier_document")
    op.execute("DROP VIEW agent_read.reliance")
    for statement in OLD_VIEWS:
        op.execute(statement)
    op.execute("DROP FUNCTION agent_read.run_snapshot()")
    op.execute("ALTER TABLE agent_tool_log DROP COLUMN agent, DROP COLUMN result_chars, DROP COLUMN ms")
