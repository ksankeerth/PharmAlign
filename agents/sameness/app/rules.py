"""What is specific to the Sameness agent: the first message and the rules a result must keep."""
import json

from pharmalign_agent_common import ToolLog, check_evidence
from pharmalign_agent_common.evidence import value_in_quotes
from pharmalign_contracts.agents.run_agents import SamenessRequest, SamenessResult


def build_message(request: SamenessRequest) -> str:
    """The run's input for the model. Tools supply the documents' text."""
    task = request.model_dump(mode="json", include={"language", "as_of", "case", "reference", "fields", "previous_rejection"})
    return json.dumps(task, ensure_ascii=False)


def check_result(request: SamenessRequest, result: SamenessResult, log: ToolLog) -> None:
    """Mirrors the contract and the server's checks. Raises ValueError."""
    asked = {f.key: f for f in request.fields}
    keys = [f.key for f in result.fields]
    if sorted(keys) != sorted(asked):
        raise ValueError(f"return each field once, and no others: {', '.join(asked)}")
    check_evidence(result, log)
    reference_docs = set(request.reference.documents)

    for field in result.fields:
        asked_field = asked[field.key]
        there, here = field.reference_evidence, field.dossier_evidence
        for e in there:
            if log.document_of(e.anchor) not in reference_docs:
                raise ValueError(f"'{field.key}': reference_evidence must quote the reference documents, not {e.anchor}")
        for e in here:
            if log.document_of(e.anchor) in reference_docs:
                raise ValueError(f"'{field.key}': dossier_evidence must quote the case's files, not {e.anchor}")
        if field.agent_view is not None and asked_field.compare != "meaning":
            raise ValueError(f"'{field.key}' is a {asked_field.compare} field: leave agent_view null, code compares it")
        if field.there_status == "found":
            if not there:
                raise ValueError(f"'{field.key}': a found value needs a quote from the reference documents")
            if not value_in_quotes(field.there_value, field.there_number, [e.quote for e in there]):
                raise ValueError(f"'{field.key}': the reference value must appear in its own quote")
            if asked_field.compare == "number" and field.there_number is None:
                raise ValueError(f"'{field.key}' is a number field: give there_number")
            if asked_field.compare == "meaning" and field.agent_view is None:
                raise ValueError(f"'{field.key}' is a meaning field: give agent_view")
        else:
            if field.agent_view in ("same", "different"):
                raise ValueError(f"'{field.key}': without a found value, agent_view can only be unclear or null")
            if field.there_status == "not_found":
                if field.there_value is not None or field.there_number is not None:
                    raise ValueError(f"'{field.key}' is not_found, so it has no value")
                if not log.looked():
                    raise ValueError(f"'{field.key}' is not_found, but nothing was read. Read the reference first")
            if field.there_status == "ambiguous" and len(there) < 2:
                raise ValueError(f"'{field.key}' is ambiguous: quote each reference passage")
        if field.agent_view == "different" and not here:
            raise ValueError(f"'{field.key}': a difference needs a dossier_evidence quote too (get_facts or read_page)")
