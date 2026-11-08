"""The Terraform files set environment variables by name. A misspelt one would be ignored and the service would start with a default."""
import re
from pathlib import Path

from app.config import Settings

TERRAFORM = Path(__file__).resolve().parents[2] / "infra" / "terraform"


def names(path: Path) -> set[str]:
    return set(re.findall(r"\b(PHARMALIGN_[A-Z0-9_]+)\b", path.read_text()))


def test_every_setting_the_server_service_sets_exists():
    fields = set(Settings.model_fields)
    found = names(TERRAFORM / "server.tf")
    assert {"PHARMALIGN_ENV", "PHARMALIGN_AGENT_MODE", "PHARMALIGN_AGENTS_OFF", "PHARMALIGN_DATABASE_URL", "PHARMALIGN_AGENT_DATABASE_URL"} <= found
    unknown = {n for n in found if n.removeprefix("PHARMALIGN_").lower() not in fields and not n.startswith("PHARMALIGN_AGENT_URL_")}
    assert not unknown, f"Settings has no field for {sorted(unknown)}"


def test_the_agent_urls_are_set_for_every_agent_that_is_on():
    variables = (TERRAFORM / "variables.tf").read_text()
    on = re.search(r'variable "agents_on".*?default\s*=\s*\[(.*?)\]', variables, re.S).group(1)
    for agent in re.findall(r'"([a-z-]+)"', on):
        assert "agent_url_" + agent.replace("-", "_") in Settings.model_fields, agent


def test_the_agents_off_default_matches_what_the_server_accepts():
    variables = (TERRAFORM / "variables.tf").read_text()
    off = re.search(r'variable "agents_off".*?default\s*=\s*\[(.*?)\]', variables, re.S).group(1)
    named = ",".join(re.findall(r'"([a-z-]+)"', off))
    assert Settings(agents_off=named).off_agents == {"challenger", "sameness", "reliance-watch"}


def test_the_agent_service_sets_the_variables_the_agents_read():
    read = (Path(__file__).resolve().parents[2] / "agents" / "common" / "pharmalign_agent_common" / "settings.py").read_text()
    for name in names(TERRAFORM / "modules" / "agent_service" / "main.tf"):
        assert name in read, name


def agent_env_block(agent: str) -> str:
    text = (TERRAFORM / "agents.tf").read_text()
    start = text.index(f'"{agent}" = {{')
    return text[start:text.index("\n    }\n", start)]


def test_the_knowledge_assistant_runs_the_chat_pipeline_in_the_cloud():
    """Without PHARMALIGN_KA_PIPELINE=on the cloud runs the model-driven loop, which the server cuts off at 60 s on many questions."""
    block = agent_env_block("knowledge-assistant")
    assert re.search(r'PHARMALIGN_KA_PIPELINE\s*=\s*"on"', block)
    assert 'env             = lookup(local.agent_env, each.value, {})' in (TERRAFORM / "agents.tf").read_text()
    module = (TERRAFORM / "modules" / "agent_service" / "main.tf").read_text()
    assert 'for_each = var.env' in module


def test_every_setting_the_knowledge_assistant_service_sets_is_read_by_the_agent():
    agent = Path(__file__).resolve().parents[2] / "agents" / "knowledge-assistant" / "app"
    code = "\n".join(p.read_text() for p in agent.rglob("*.py"))
    for name in names_in(agent_env_block("knowledge-assistant")):
        assert name in code, f"{name} is set in agents.tf and read nowhere in the agent"


def names_in(text: str) -> set[str]:
    return set(re.findall(r"\b(PHARMALIGN_[A-Z0-9_]+)\b", text))


def test_the_brief_languages_setting_narrows_only_the_coordinators_drafts():
    assert Settings().languages_for_brief(["en", "ar"]) == ["en", "ar"]  # unset: the pack's languages
    assert Settings(brief_languages="en").languages_for_brief(["en", "ar"]) == ["en"]
    assert Settings(brief_languages=" en , fr ").languages_for_brief(["en", "ar"]) == ["en", "fr"]


def test_the_cloud_drafts_questions_in_english_only():
    text = (TERRAFORM / "variables.tf").read_text()
    block = text[text.index('variable "brief_languages"'):]
    assert re.search(r'default\s*=\s*\["en"\]', block[:block.index("}")])
