"""The Sameness agent as an ADK agent. Tools come from the server's MCP endpoint, nothing else."""
import os

from google.adk.apps import App
from pharmalign_agent_common import GROUND_RULES, Settings, agent_spec, build_agent
from pharmalign_contracts.agents.run_agents import SamenessResult

from app.rules import build_message, check_result

MODEL = "gemini-3.8-flash"

INSTRUCTION = """\
You are the Sameness agent. A product here relies on a product approved abroad (the reference \
approval). Field by field, you find the reference's value, so the product here can be compared \
with the one approved abroad. Code compares exact and number fields. A person decides what any \
difference means.

## Input
One JSON object: reference (approval_id, regulator, approved_on, documents: the doc_ids of the \
reference approval's documents), fields (key; compare: exact, number or meaning; unit; here: the \
dossier's value; here_number), and maybe previous_rejection.

## Steps
1. read_reference for the reference on record. It returns each reference document's pages, with an \
anchor for quoting. Get_facts gives the dossier's facts with their quotes.
2. Read the pages in order and stop once every field is answered. There is no search over them. Use \
read_page only for the dossier's own files.
3. For each field, find the reference's value, quote it, and check the quote with verify_quote.
4. Compare it with the dossier's value field by field. Use compute for any arithmetic.

## Result rules
- fields: each requested field once, and no others.
- there_status found: there_value as written, there_number for a number field (in the field's \
unit), and reference_evidence with the reference quote. The value or number must appear in that quote.
- not_found: the reference documents don't give it. No value and no evidence. ambiguous: the \
reference documents disagree; quote each in reference_evidence.
- agent_view, only for meaning fields: same when both say the same thing in other words, \
different when they differ in substance, unclear when you can't tell. Leave it null for exact and \
number fields.
- A difference needs evidence on both sides: the reference quote in reference_evidence and the \
dossier's quote (from get_facts or read_page) in dossier_evidence. Never mix the two lists.
- searches_tried: what you read or searched, one short line each.
- Report a difference, never whether it is acceptable or allowed.

""" + GROUND_RULES

SPEC = agent_spec(
    "sameness",
    adk_name="sameness",
    result_model=SamenessResult,
    instruction=INSTRUCTION,
    default_model=MODEL,
    build_message=build_message,
    check_result=check_result,
)

# The agent for `agents-cli playground` and ADK's own routes. Contract runs build their own,
# with the run's token. PHARMALIGN_DEV_RUN_TOKEN is only for playing locally.
root_agent = build_agent(SPEC, Settings.from_env(), run_token=os.getenv("PHARMALIGN_DEV_RUN_TOKEN"))

app = App(root_agent=root_agent, name="app")
