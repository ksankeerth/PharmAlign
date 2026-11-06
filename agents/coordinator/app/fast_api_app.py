"""The service: ADK's own routes, with the contract routes (/briefs) in front and one /health."""
import os

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()  # before the agent module reads its settings

from pharmalign_agent_common import adk_app  # noqa: E402

from app import coordinator  # noqa: E402
from app.agent import SPEC  # noqa: E402

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app: FastAPI = adk_app(SPEC, coordinator.write_brief, AGENT_DIR)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
