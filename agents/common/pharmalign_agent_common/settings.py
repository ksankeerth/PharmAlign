"""Settings read from the environment.

PHARMALIGN_MCP_URL         streamable HTTP URL of the server's MCP tools
PHARMALIGN_MODEL_MODE      "vertex", "api_key" or "scripted" (default: api_key if GEMINI_API_KEY or
                           GOOGLE_API_KEY is set, else vertex). "scripted" follows recorded scripts
                           under demo/scripted, needs no key, and is refused for live requests
PHARMALIGN_MODEL_<AGENT>   model name for one agent, e.g. PHARMALIGN_MODEL_DOSSIER_READER
GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION   used in vertex mode
GEMINI_API_KEY             used in api_key mode
PHARMALIGN_OTEL_TO_CLOUD   "true" exports telemetry to Google Cloud. Off unless set (tests, local runs)
"""
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

ModelMode = Literal["vertex", "api_key", "scripted"]


def _env_key(agent_name: str) -> str:
    return "PHARMALIGN_MODEL_" + agent_name.upper().replace("-", "_")


@dataclass(frozen=True)
class Settings:
    mcp_url: str | None = None
    model_mode: ModelMode = "vertex"
    model_overrides: Mapping[str, str] = field(default_factory=dict)
    mcp_timeout_seconds: float = 10.0
    otel_to_cloud: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        mode = env.get("PHARMALIGN_MODEL_MODE")
        if mode == "scripted" and env.get("K_SERVICE"):
            raise RuntimeError("PHARMALIGN_MODEL_MODE=scripted is not allowed on Cloud Run")
        if mode not in ("vertex", "api_key", "scripted"):
            has_key = env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY")
            mode = "api_key" if has_key else "vertex"
        overrides = {k: v for k, v in env.items() if k.startswith("PHARMALIGN_MODEL_") and k != "PHARMALIGN_MODEL_MODE" and v}
        return cls(
            mcp_url=env.get("PHARMALIGN_MCP_URL") or None,
            model_mode=mode,  # type: ignore[arg-type]
            model_overrides=overrides,
            otel_to_cloud=env.get("PHARMALIGN_OTEL_TO_CLOUD", "false").strip().lower() in ("true", "1", "yes"),
        )

    def model_for(self, agent_name: str, default: str) -> str:
        return self.model_overrides.get(_env_key(agent_name), default)

    def apply_model_mode(self) -> None:
        """Points the Google GenAI client at Vertex or the API-key endpoint. Scripted mode uses neither."""
        if self.model_mode == "scripted":
            return
        flag = "true" if self.model_mode == "vertex" else "false"
        os.environ["GOOGLE_GENAI_USE_ENTERPRISE"] = flag
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = flag
