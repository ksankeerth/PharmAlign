"""Settings, read from PHARMALIGN_* environment variables."""
from typing import Literal, Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PHARMALIGN_", extra="ignore")

    # Defaults to live, so demo identity only ever runs when someone asks for simulation.
    env: Literal["simulation", "shadow", "live"] = "live"
    database_url: str = "postgresql+asyncpg://pharmalign:pharmalign@localhost:5544/pharmalign"
    # Bearer token for non-person callers, such as the scheduler. Unset refuses them all.
    service_token: Optional[str] = None
    default_tenant: str = "ws-ae"
    default_person: str = "p-alex"
    worker_enabled: bool = True
    worker_id: Optional[str] = None  # stable across restarts; defaults to the host name
    worker_poll_seconds: float = 1.0
    job_lease_seconds: float = 60.0
    # A source that yields nothing for this many days is called silent, and unhealthy.
    watch_silence_days: int = 14
    # Agents. Replay needs no URLs and is the default in simulation.
    agent_mode: Optional[Literal["http", "replay"]] = None
    agent_url_dossier_reader: Optional[str] = None
    agent_url_checklist_assessor: Optional[str] = None
    agent_url_sameness: Optional[str] = None
    agent_url_reliance_watch: Optional[str] = None
    agent_url_challenger: Optional[str] = None
    agent_url_coordinator: Optional[str] = None
    agent_url_gap_analyst: Optional[str] = None
    agent_url_knowledge_assistant: Optional[str] = None
    agent_token: Optional[str] = None
    agent_connect_seconds: float = 5.0
    agent_read_seconds: float = 30.0
    agent_retries: int = 2
    agent_poll_seconds: Optional[float] = None  # 1 in simulation, where answers are stored, otherwise 5
    agent_deadline_grace_seconds: float = 30.0
    agent_run_deadline_seconds: int = 180
    agent_max_model_calls: int = 12
    agent_max_tool_calls: int = 20
    # The government system of record. In simulation the built-in mock at /mock is the default.
    connector_base_url: Optional[str] = None
    connector_token: Optional[str] = None
    connector_connect_seconds: float = 3.0
    connector_timeout_seconds: float = 10.0
    connector_retry_seconds: float = 5.0  # first wait after an unknown outcome; doubles up to five minutes
    # None mounts the mock regulator in simulation only. True anywhere else refuses to start.
    mock_regulator: Optional[bool] = None
    mock_regulator_hang_seconds: float = 30.0  # how long a "timed out" call stays silent
    mock_regulator_slow_seconds: float = 3.0
    # Knowledge. `auto` is bge-m3 when installed, else the deterministic embedder in simulation. The floor is the least similarity that counts as a source.
    embedder: Literal["auto", "hash", "bge-m3"] = "auto"
    similarity_floor: Optional[float] = None
    # Every question: bge-reranker-v2-m3 reorders the best of vector and keyword search. It needs the bge-m3 extra.
    reranker: Literal["off", "bge-reranker-v2-m3"] = "off"
    rerank_candidates: int = 15
    rerank_floor: float = 0.0
    embed_batch: int = 32  # chunks embedded per job step
    fetch_max_bytes: int = 60_000_000
    # Signs the run token that scopes an agent's tools. Required outside simulation.
    run_token_secret: Optional[str] = None
    # The agent tool server at /mcp. None turns it on in simulation only.
    mcp_enabled: Optional[bool] = None
    # The five views act as two roles (see authz.ROLE_GROUPS). On in simulation, off elsewhere.
    two_roles: Optional[bool] = None
    # Read Arabic PDFs from their text layer, with app/knowledge/arabic.py repairing it. The quality gate still applies.
    arabic_text_layer: bool = False
    # The pharmalign_agent login. Unset uses the database URL with the development password, in simulation only.
    agent_database_url: Optional[str] = None
    mcp_max_rows: int = 50  # rows in one result
    mcp_max_chars: int = 12_000  # characters in one result
    assistant_session_seconds: int = 8 * 3600  # how long an assistant session's token lasts
    assistant_max_tool_calls: int = 200

    @model_validator(mode="after")
    def _poll_default(self) -> "Settings":
        if self.agent_poll_seconds is None:
            self.agent_poll_seconds = 1.0 if self.env == "simulation" else 5.0
        if self.mcp_enabled is None:
            self.mcp_enabled = self.env == "simulation"
        if self.two_roles is None:
            self.two_roles = self.env == "simulation"
        if self.env == "simulation":
            self.connector_base_url = self.connector_base_url or "http://127.0.0.1:8000/mock"
            self.connector_token = self.connector_token or "simulation-regulator-token"
        return self
