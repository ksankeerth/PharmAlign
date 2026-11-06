"""The platform's standard list of required document slots. A new workspace starts from it, with every slot empty.
The keys and labels name a kind of document, never one country's law, so any country can use them."""
from typing import NamedTuple

from pharmalign_contracts.server.models import Slot


class SlotDefinition(NamedTuple):
    key: str
    label: str
    importance: str  # must or nice


PLATFORM_SLOTS: tuple[SlotDefinition, ...] = (
    SlotDefinition("medicines_law", "Medicines law in force", "must"),
    SlotDefinition("medicines_law_regulations", "Executive regulations of the medicines law", "must"),
    SlotDefinition("fees_schedule", "Service fees", "must"),
    SlotDefinition("registration_service", "Marketing authorisation service", "must"),
    SlotDefinition("ctd_structure", "Dossier structure (CTD)", "must"),
    SlotDefinition("stability_guideline", "Stability testing guideline", "must"),
    SlotDefinition("labelling_guideline", "Product information and labelling", "must"),
    SlotDefinition("pv_guideline", "Pharmacovigilance guideline", "must"),
    SlotDefinition("gmp_guideline", "Good manufacturing practice", "must"),
    SlotDefinition("reliance_guidance", "Reliance practice", "must"),
    SlotDefinition("variations_guideline", "Variations", "nice"),
    SlotDefinition("recall_policy", "Recall procedure", "nice"),
    SlotDefinition("pricing_rules", "Pricing rules", "nice"),
    SlotDefinition("child_resistant_packaging", "Child-resistant packaging", "nice"),
)


def empty_slot(slot_id: str, definition: SlotDefinition) -> Slot:
    """A slot with its definition only: no document, no gap, no notes."""
    return Slot(id=slot_id, key=definition.key, label=definition.label, importance=definition.importance, status="missing")
