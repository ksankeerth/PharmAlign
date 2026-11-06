"""The run token: what one agent run or assistant session may read, signed by the server.

Format: `pa1.<claims>.<signature>`, both parts base64url without padding. The claims are JSON; the signature is HMAC-SHA256 of
`pa1.<claims>` with PHARMALIGN_RUN_TOKEN_SECRET. The tool server takes tenant, case and snapshot from here and nowhere else.
"""
import base64
import hmac
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

from ..config import Settings

PREFIX = "pa1"
SIMULATION_ONLY_SECRET = "simulation-only"
ASSISTANT = "knowledge-assistant"


class RunTokenSecretMissing(RuntimeError):
    """Outside simulation, a run token needs a real secret."""


class BadToken(Exception):
    """Not a token this server signed."""


@dataclass(frozen=True)
class RunScope:
    kind: Literal["run", "session"]
    tenant: str
    case: Optional[str]  # None for a knowledge-only session
    agent: str
    id: str  # the run id, or the session id
    snapshot: str
    calls: int  # tool calls allowed
    exp: int  # unix seconds

    @property
    def expires_at(self) -> datetime:
        return datetime.fromtimestamp(self.exp, timezone.utc)


def _secret(settings: Settings) -> bytes:
    secret = settings.run_token_secret
    if not secret:
        if settings.env != "simulation":
            raise RunTokenSecretMissing("Set PHARMALIGN_RUN_TOKEN_SECRET.")
        secret = SIMULATION_ONLY_SECRET
    return secret.encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(settings: Settings, scope: RunScope) -> str:
    body = f"{PREFIX}.{_b64(json.dumps(asdict(scope), separators=(',', ':'), sort_keys=True).encode())}"
    return f"{body}.{_b64(hmac.new(_secret(settings), body.encode(), 'sha256').digest())}"


def read(settings: Settings, token: str) -> RunScope:
    """Checks the signature only. Whether the run is still current is the tool server's job."""
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise BadToken("not a run token")
    body = f"{parts[0]}.{parts[1]}"
    try:
        expected = hmac.new(_secret(settings), body.encode(), "sha256").digest()
        if not hmac.compare_digest(expected, _unb64(parts[2])):
            raise BadToken("bad signature")
        return RunScope(**json.loads(_unb64(parts[1])))
    except BadToken:
        raise
    except Exception as e:  # malformed base64, JSON or fields
        raise BadToken("malformed token") from e


def run_token(settings: Settings, *, run_id: str, agent: str, tenant: str, case_id: str, snapshot_id: str, max_calls: int, expires_at: datetime) -> str:
    return sign(settings, RunScope("run", tenant, case_id, agent, run_id, snapshot_id, max_calls, int(expires_at.timestamp())))


def session_token(settings: Settings, *, session_id: str, tenant: str, case_id: Optional[str], snapshot_id: str, max_calls: int, expires_at: datetime) -> str:
    return sign(settings, RunScope("session", tenant, case_id, ASSISTANT, session_id, snapshot_id, max_calls, int(expires_at.timestamp())))
