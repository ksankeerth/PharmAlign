"""Follow-up questions on the subject of the person's question, for the turns where no answer model wrote any.

When the library has nothing for a question, or the answer model gave no suggestions, the chips shown under the reply must still be about
the same subject, and each one must be a question the library answers. No model writes these: a short list per subject, each question
checked by hand against the library. A question that matches no subject gets the general three in app/replies.py.
"""
import re
from collections.abc import Sequence

# (subject words, questions). The first subject whose words appear in the question wins, so order the narrow ones first.
TOPICS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = tuple(
    (re.compile(words, re.IGNORECASE), questions)
    for words, questions in (
        (r"\b(controlled|narcotic\w*|psychotropic|opioid\w*|cannabis|cannabinoid\w*|addictive)\b", (
            "Which medicines are controlled medicines in the UAE?",
            "What rules apply to travellers bringing controlled medicines into the UAE?",
            "What is needed for a permit to import medical products?",
        )),
        (r"\b(nicotine|vap\w*|tobacco|cigarette\w*|e-?liquid\w*|smok\w*|supplement\w*|cosmetic\w*|herbal|borderline|food|beauty)\b", (
            "How is a medical product classified?",
            "What is a Health Care Product under UAE law?",
            "Which medicines are controlled medicines in the UAE?",
        )),
        (r"\b(advertis\w*|promot\w*|brochure\w*|marketing material)\b", (
            "What is needed to license an advertisement for a medical product?",
            "How is a medical product classified?",
            "What is the fee for the marketing authorization of a conventional pharmaceutical product?",
        )),
        (r"\b(import\w*|export\w*|customs|shipment\w*)\b", (
            "What is needed for a permit to import medical products?",
            "How is a certificate of a pharmaceutical product for export issued?",
            "What rules apply to travellers bringing controlled medicines into the UAE?",
        )),
        (r"\b(adverse|side[- ]effects?|pharmacovigilance|safety report\w*|vigilance)\b", (
            "What is the deadline for reporting a serious adverse reaction?",
            "How can an individual report a side effect of a medicine?",
            "Who approves a pharmacovigilance officer for a pharmaceutical establishment?",
        )),
        (r"\b(recall\w*|quarantine\w*|withdraw\w*|counterfeit\w*|falsified)\b", (
            "What does the DoH policy for quarantine and recall of medical products require?",
            "What is the deadline for reporting a serious adverse reaction?",
            "How can an individual report a side effect of a medicine?",
        )),
        (r"\b(pharmac\w*|licen[cs]\w*|establishment\w*|warehouse\w*|distribut\w*|dispens\w*|wholesal\w*)\b", (
            "What are the license conditions for a general pharmacy?",
            "Who may own a pharmaceutical establishment?",
            "What is needed for a certificate of compliance with good practice standards?",
        )),
        (r"\b(fees?|prices?|pricing|cost\w*|pay\w*|charge\w*)\b", (
            "What is the fee for the marketing authorization of a conventional pharmaceutical product?",
            "How is a single medical product priced?",
            "How long does a marketing approval for a medicine stay valid?",
        )),
        (r"\b(data|privacy|personal information|patient records?|confidential\w*|cyber\w*|AI|artificial intelligence|algorithm\w*)\b", (
            "How must health data be classified?",
            "What does the Responsible AI standard of DoH Abu Dhabi require?",
            "What do the standards say about consent to share health information?",
        )),
        (r"\b(trials?|clinical|research|bioequivalence|investigator\w*)\b", (
            "What do the DoH guidelines for conducting clinical trials require?",
            "How is a medical product classified?",
            "How long does a marketing approval for a medicine stay valid?",
        )),
        (r"\b(registration|register\w*|marketing (approval|authori[sz]ation)|renew\w*|approval)\b", (
            "How long does a marketing approval for a medicine stay valid?",
            "What is the fee for the marketing authorization of a conventional pharmaceutical product?",
            "When must the renewal of a marketing approval be submitted?",
        )),
    )
)


def suggestions_for(parts: Sequence[str]) -> tuple[str, ...]:
    """Up to three questions on the subject of `parts`, or () when no subject matches. Never a question the person just asked."""
    text = " ".join(parts)
    asked = {" ".join(p.casefold().split()).rstrip("?. ") for p in parts}
    for pattern, questions in TOPICS:
        if pattern.search(text):
            return tuple(q for q in questions if " ".join(q.casefold().split()).rstrip("?. ") not in asked)[:3]
    return ()
