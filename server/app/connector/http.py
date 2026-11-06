"""The adapter for any system that serves the regulator records API (contracts/pharmalign_contracts/regulator.py)."""
import asyncio
from typing import Optional

import httpx
from pharmalign_contracts.regulator import ProposalCreate, ProposalStatus
from pydantic import ValidationError

from ..config import Settings
from .adapter import ConnectorError, NotConfigured, Outcome, ReadBack

REJECTED_BY_REQUEST = (400, 409, 422)  # the system looked at it and said no


def _outcome(status: ProposalStatus) -> Outcome:
    if status.state == "applied":
        return Outcome("applied", gov_ref=status.gov_ref)
    if status.state == "rejected":
        return Outcome("rejected", reason=status.reason or "Rejected by the government system.")
    return Outcome("unknown", reason="The government system received the proposal and hasn't decided it.")


class HttpConnector:
    def __init__(self, base_url: Optional[str], token: Optional[str], connect_seconds: float = 3.0, timeout_seconds: float = 10.0,
                 transport: Optional[httpx.AsyncBaseTransport] = None):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token
        self.timeout = timeout_seconds
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds, connect=connect_seconds), transport=transport)

    async def aclose(self) -> None:
        await self.client.aclose()

    def _headers(self, **extra) -> dict:
        return {**({"Authorization": f"Bearer {self.token}"} if self.token else {}), **extra}

    async def _call(self, method: str, path: str, **kw) -> httpx.Response:
        if not self.base_url:
            raise NotConfigured("No government connection is configured.")
        # The overall deadline holds for any transport, not only the network ones.
        async with asyncio.timeout(self.timeout):
            return await self.client.request(method, f"{self.base_url}{path}", **kw)

    async def propose(self, idempotency_key: str, proposal: ProposalCreate) -> Outcome:
        try:
            r = await self._call("POST", "/proposals", content=proposal.model_dump_json(),
                                 headers=self._headers(**{"Idempotency-Key": idempotency_key, "Content-Type": "application/json"}))
        except NotConfigured as e:
            return Outcome("unknown", reason=str(e))
        except (httpx.HTTPError, TimeoutError) as e:
            return Outcome("unknown", reason=f"No answer from the government system ({type(e).__name__}).")
        if r.status_code in REJECTED_BY_REQUEST:
            return Outcome("rejected", reason=_detail(r) or f"Refused by the government system ({r.status_code}).")
        if r.status_code >= 300:
            return Outcome("unknown", reason=f"The government system answered {r.status_code}.")
        try:
            return _outcome(ProposalStatus.model_validate(r.json()))
        except (ValueError, ValidationError):
            return Outcome("unknown", reason="The government system's answer wasn't understood.")

    async def read_back(self, idempotency_key: str) -> ReadBack:
        try:
            r = await self._call("GET", "/proposals", params={"idempotency_key": idempotency_key}, headers=self._headers())
        except NotConfigured:
            raise
        except (httpx.HTTPError, TimeoutError) as e:
            raise ConnectorError(f"No answer from the government system ({type(e).__name__}).") from e
        if r.status_code != 200:
            raise ConnectorError(f"The government system answered {r.status_code}.")
        try:
            items = [ProposalStatus.model_validate(i) for i in r.json()["items"]]
        except (ValueError, KeyError, ValidationError) as e:
            raise ConnectorError("The government system's answer wasn't understood.") from e
        match = next((i for i in items if i.idempotency_key == idempotency_key), None)
        return ReadBack(_outcome(match) if match else None)


def _detail(r: httpx.Response) -> Optional[str]:
    try:
        return str(r.json().get("detail") or r.json().get("title"))[:200]
    except (ValueError, AttributeError):
        return None


def make_connector(settings: Settings, transport: Optional[httpx.AsyncBaseTransport] = None) -> HttpConnector:
    return HttpConnector(settings.connector_base_url, settings.connector_token, settings.connector_connect_seconds, settings.connector_timeout_seconds, transport)
