"""What is specific to the Dossier Reader: the first message and the rules a result must keep."""
import json

from pharmalign_agent_common import ToolLog, check_evidence
from pharmalign_agent_common.evidence import fact_in_quotes
from pharmalign_contracts.agents.run_agents import DossierReadRequest, DossierReadResult


def build_message(request: DossierReadRequest) -> str:
    """The run's input for the model. The request holds no document text: tools supply it. The budget is included so it can plan."""
    task = request.model_dump(
        mode="json",
        include={"language", "as_of", "budget", "documents", "changed_doc_ids", "modules", "fact_keys", "previous_rejection"},
    )
    return json.dumps(task, ensure_ascii=False)


def check_result(request: DossierReadRequest, result: DossierReadResult, log: ToolLog) -> None:
    """The rules the server enforces on every result, checked here first. Raises ValueError."""
    asked = {f.key for f in request.fact_keys}
    doc_ids = {d.doc_id for d in request.documents}
    keys = [f.key for f in result.facts]
    for fact in result.facts:
        if fact.key not in asked:
            raise ValueError(f"fact '{fact.key}' wasn't asked for")
        for ev in fact.evidence:
            if ev.anchor.split(":")[0] not in doc_ids:
                raise ValueError(f"anchor '{ev.anchor}' points at a document that isn't in this run")
    if len(keys) != len(set(keys)):
        raise ValueError("each fact key may appear only once")
    for entry in result.document_map:
        if entry.doc_id not in doc_ids:
            raise ValueError(f"document_map names '{entry.doc_id}', which isn't in this run")
        if entry.module is not None and entry.module not in request.modules:
            raise ValueError(f"module '{entry.module}' isn't one of the given modules")
    for issue in result.issues:
        if issue.doc_id not in doc_ids:
            raise ValueError(f"issue names '{issue.doc_id}', which isn't in this run")

    check_evidence(result, log)
    for fact in result.facts:
        if not fact_in_quotes(fact):
            raise ValueError(f"fact '{fact.key}': the value must appear in its own quote")
        if fact.status == "ambiguous" and len(fact.evidence) < 2:
            raise ValueError(f"fact '{fact.key}' is ambiguous: give a quote for each side")
        if fact.status == "not_found" and not log.looked():
            raise ValueError(f"fact '{fact.key}' is not_found, but nothing was searched or read. Search first")
