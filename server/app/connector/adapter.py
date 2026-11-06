"""What the record-sending job needs from a government connector."""
from dataclasses import dataclass
from typing import Literal, Optional, Protocol

from pharmalign_contracts.regulator import ProposalCreate


class ConnectorError(Exception):
    """The government system couldn't be asked. What it did with a proposal is unknown."""


class NotConfigured(ConnectorError):
    """There is no connection to send through."""


@dataclass(frozen=True)
class Outcome:
    """`unknown` covers a timeout, a dropped connection, a 5xx, and a proposal received but not yet decided."""

    state: Literal["applied", "rejected", "unknown"]
    gov_ref: Optional[str] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class ReadBack:
    """What the government system holds for an idempotency key. `outcome` is None when it holds nothing."""

    outcome: Optional[Outcome]


class ConnectorAdapter(Protocol):
    async def propose(self, idempotency_key: str, proposal: ProposalCreate) -> Outcome:
        """Sends the proposal with its key. Never raises for a failed call: that is an unknown outcome."""

    async def read_back(self, idempotency_key: str) -> ReadBack:
        """Asks what the government system holds for the key. Raises ConnectorError if it can't tell."""
