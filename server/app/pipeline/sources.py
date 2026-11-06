"""The text a citation can be checked against, read from the database: the pinned knowledge snapshot, foreign notices and reference documents.

Both the runner (for an agent's evidence) and the assistant (for an answer's citations) build their citation context from these.
"""
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..tables import ROW
from .citations import SourceChunk


async def kb_chunks(db: AsyncSession, tenant_id: str, snapshot_id: str, anchors: Iterable[str]) -> dict[str, SourceChunk]:
    """Every chunk of each cited version, as that version sits in the snapshot. The rest of a document is needed to tell a quote that points here."""
    chunk, item, version = ROW["chunk"], ROW["kb_snapshot_item"], ROW["document_version"]
    cited = select(chunk.document_version_id).where(chunk.id.in_(set(anchors)))
    rows = await db.execute(
        select(chunk.id, chunk.text, chunk.document_version_id, version.document_id, item.legal_status)
        .join(version, version.id == chunk.document_version_id)
        .join(item, item.document_version_id == chunk.document_version_id)
        .where(item.snapshot_id == snapshot_id, item.tenant_id == tenant_id, chunk.document_version_id.in_(cited))
    )
    return {a: SourceChunk(a, text, "kb", version_id, document_id, in_force=status == "in_force") for a, text, version_id, document_id, status in rows.all()}


async def notice_chunks(db: AsyncSession, tenant_id: str, anchors: Iterable[str]) -> dict[str, SourceChunk]:
    """A foreign notice's text at its latest revision, the same text the tool layer reads. An older revision is no longer what the regulator says."""
    revision, notice = ROW["foreign_notice_revision"], ROW["foreign_notice"]
    rows = await db.execute(
        select(revision.text_anchor, revision.text, revision.notice_id, revision.revision)
        .join(notice, (notice.id == revision.notice_id) & (notice.latest_revision == revision.revision))
        .where(revision.tenant_id == tenant_id, notice.tenant_id == tenant_id, revision.text_anchor.in_(set(anchors)))
    )
    return {a: SourceChunk(a, text or "", "notice", f"{notice_id}:r{rev}", notice_id) for a, text, notice_id, rev in rows.all()}


async def reference_chunks(db: AsyncSession, tenant_id: str, case_id: str, anchors: Iterable[str]) -> dict[str, SourceChunk]:
    """Every page of each cited reference document, for the approval this case relies on. Nothing else is a reference."""
    page, basis = ROW["reference_page"], ROW["reliance_basis"]
    cited = {a.split(":")[0] for a in anchors}
    rows = await db.execute(
        select(page.id, page.text, page.document_id, page.version)
        .join(basis, basis.id == page.basis_id)
        .where(page.tenant_id == tenant_id, basis.tenant_id == tenant_id, basis.target_type == "case", basis.target_id == case_id, page.document_id.in_(cited))
    )
    return {a: SourceChunk(a, text, "reference", f"{doc}:{version}", doc) for a, text, doc, version in rows.all()}
