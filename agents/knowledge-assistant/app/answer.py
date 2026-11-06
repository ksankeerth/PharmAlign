"""What the model answers each turn: a short list of events. Only these four types are allowed.
The agent adds text_delta and usage itself, and an error when a turn fails."""
from typing import Annotated, Union

from pharmalign_contracts.agents.knowledge_assistant import DraftComment, Message, NoSource, NotAvailable
from pharmalign_contracts.common import Strict
from pydantic import Field

MODEL_EVENTS = frozenset({"message", "no_source", "not_available", "draft_comment"})


class TurnAnswer(Strict):
    events: Annotated[
        list[Annotated[Union[Message, NoSource, NotAvailable, DraftComment], Field(discriminator="type")]],
        Field(min_length=1, max_length=4),
    ]
