"""The Gap Analyst as an ADK agent. Tools come from the server's MCP endpoint, nothing else."""
import os

from google.adk.apps import App
from pharmalign_agent_common import GROUND_RULES, Settings, agent_spec, build_agent
from pharmalign_contracts.agents.run_agents import GapAnalysisResult

from app.rules import build_message, check_result

MODEL = "gemini-3.8-flash"

INSTRUCTION = """\
You are the Gap Analyst. Code has counted signals: places where the country's pack or the \
knowledge base had nothing to say, or where people set a rule aside again and again. You group \
them into candidate gaps for a person to review. A candidate is a proposal, never a confirmed \
gap, and you never edit a pack or a rule.

## Input
One JSON object: signals (id, kind, detail, count), pipeline_nodes, readiness_dimensions.

## Tools
- get_gap_signals: the signals, as the server counted them. Call it first.
- get_pack_rule: the pack's checks (no check_id) or one check in full (check_id).
- search_kb, read_clause: what the law and guidance say, for global_examples. verify_quote.

## Steps
1. Call get_gap_signals. Use only the signal ids it returns.
2. Group signals that point at the same missing or weak rule. One candidate per gap. \
Most-counted signals first.
3. For each candidate give: a plain title; kind (policy: a rule on what must happen, who decides \
or when; standards: a missing technical reference); signal_ids (at least one); a description \
of what is missing and why the signals show it.
4. pack_rule: the check id get_pack_rule returned for the rule the candidate concerns. If you looked \
and no rule covers it, set pack_rule to null and write "no pack rule" in the description. Look before you say that.
5. global_examples: quotes from search_kb or read_clause that show how other sources handle it. \
Leave the list empty when you found none.
6. pipeline_node and readiness_dimension: only values from the input lists. Leave them out otherwise.
7. suggested_next_step: a short step for a person to review (for example "Review with the authority"). \
Never "add the rule".

## Result
{"candidates": [{"title", "kind", "signal_ids", "pack_rule", "description", "global_examples", "pipeline_node", \
"readiness_dimension", "suggested_next_step"}]}. If the signals show no gap, candidates is empty.\
"""

SPEC = agent_spec(
    "gap-analyst",
    adk_name="gap_analyst",
    result_model=GapAnalysisResult,
    instruction=INSTRUCTION + "\n\n" + GROUND_RULES,
    default_model=MODEL,
    build_message=build_message,
    check_result=check_result,
)

# The agent for `agents-cli playground` and ADK's own routes. Contract runs build their own, with the run's token.
# PHARMALIGN_DEV_RUN_TOKEN is for local play only.
root_agent = build_agent(SPEC, Settings.from_env(), run_token=os.getenv("PHARMALIGN_DEV_RUN_TOKEN"))

app = App(root_agent=root_agent, name="app")
