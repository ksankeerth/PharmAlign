"""The language gate. Decides from the script alone, with no model call, whether a message needs the "English only" reply.

Rule: a message needs the reply when more than 30% of its letters are Arabic script. A short Arabic term inside an
English sentence does not trip it. Other non-English text (French, for example) is the classifier's job.
"""

ARABIC = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)
THRESHOLD = 0.30


def _is_arabic(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in ARABIC)


def arabic_share(text: str) -> float:
    """Arabic-script letters divided by all letters. 0.0 when there are no letters."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if _is_arabic(c)) / len(letters)


def needs_english(text: str) -> bool:
    return arabic_share(text) > THRESHOLD
