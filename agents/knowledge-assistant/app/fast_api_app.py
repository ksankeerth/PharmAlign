"""The service: ADK's own routes, with the session routes (/sessions) in front and one /health.

PHARMALIGN_KA_PIPELINE=on  knowledge-only sessions run the chat pipeline (app/chat). Case sessions keep the ADK agent.
PHARMALIGN_KA_TURN_LOG     optional path: one JSON line per pipeline turn.
"""
import os

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()  # before the agent module reads its settings

from pharmalign_agent_common import adk_session_app  # noqa: E402

from app.agent import SPEC  # noqa: E402

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_app() -> FastAPI:
    if os.getenv("PHARMALIGN_KA_PIPELINE", "off").strip().lower() != "on":
        return adk_session_app(SPEC, AGENT_DIR)
    from pharmalign_agent_common import Settings, attach_first
    from pharmalign_agent_common.serving import _adk_base

    from app.chat.router import build_pipeline_router

    settings = Settings.from_env()
    pipeline_app = _adk_base(SPEC.name, AGENT_DIR, settings)
    router = build_pipeline_router(SPEC, settings, health=False, turn_log_path=os.getenv("PHARMALIGN_KA_TURN_LOG") or None)
    attach_first(pipeline_app, router)
    return pipeline_app


app: FastAPI = build_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
