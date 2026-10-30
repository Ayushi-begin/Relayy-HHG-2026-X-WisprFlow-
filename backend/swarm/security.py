"""Input hardening for ticket text: PII redaction and manipulation-attempt detection.

Both run in plain Python, before and independently of any model call, so they hold even
if a model is fooled.
"""

import re
from dataclasses import dataclass, field

CARD_PLACEHOLDER = "[REDACTED CARD]"
PHONE_PLACEHOLDER = "[REDACTED PHONE]"

# 13-19 digits, optionally grouped with single spaces or dashes.
_CARD = re.compile(r"(?<![\w$.])\d(?:[ -]?\d){12,18}(?![\w])")
# International (+...) or North-American-style numbers; digit count is checked separately.
_PHONE_INTL = re.compile(r"(?<![\w$.])\+\d[\d ().-]{7,16}\d(?![\w])")
_PHONE_LOCAL = re.compile(r"(?<![\w$.,])(?:\+?1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]?\d{3}[ .-]?\d{4}(?!\w|\.\d)")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _is_card(match: re.Match[str]) -> bool:
    digits = re.sub(r"\D", "", match.group())
    # Luhn-valid numbers, or any 15/16 digit number (typos in a real card still shouldn't go to a model).
    return 13 <= len(digits) <= 19 and (_luhn(digits) or len(digits) in (15, 16))


def _is_phone(match: re.Match[str]) -> bool:
    digits = re.sub(r"\D", "", match.group())
    return 10 <= len(digits) <= 15 and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", match.group())


@dataclass
class Redaction:
    text: str
    cards: int = 0
    phones: int = 0

    @property
    def counts(self) -> dict[str, int]:
        return {k: v for k, v in (("cards", self.cards), ("phones", self.phones)) if v}


def redact_pii(text: str) -> Redaction:
    """Replace credit card and phone numbers with placeholders."""
    result = Redaction(text)

    def sub(pattern: re.Pattern[str], accept, placeholder: str, attr: str) -> None:
        def replace(m: re.Match[str]) -> str:
            if not accept(m):
                return m.group()
            setattr(result, attr, getattr(result, attr) + 1)
            return placeholder

        result.text = pattern.sub(replace, result.text)

    sub(_CARD, _is_card, CARD_PLACEHOLDER, "cards")  # cards first so their digits aren't read as phones
    sub(_PHONE_INTL, _is_phone, PHONE_PLACEHOLDER, "phones")
    sub(_PHONE_LOCAL, _is_phone, PHONE_PLACEHOLDER, "phones")
    return result


def count_redactions(*texts: str) -> dict[str, int]:
    total = Redaction("")
    for t in texts:
        r = redact_pii(t)
        total.cards += r.cards
        total.phones += r.phones
    return total.counts


def sanitize_model_text(text: str) -> str:
    """Apply selected-pattern redaction before text crosses a model boundary.

    This deliberately redacts only the card and phone patterns defined above. It is
    not a general claim that all personally identifiable information has been removed.
    """
    return redact_pii(text).text


# --- Manipulation detection ----------------------------------------------------------

_SIGNALS: dict[str, re.Pattern[str]] = {
    "ignore_instructions": re.compile(
        r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|your|the)\b[^.\n]{0,30}"
        r"\b(instructions?|rules?|prompts?|polic(?:y|ies)|guidelines?)\b", re.I),
    "fake_system_message": re.compile(r"\b(system\s*(override|prompt|message|instruction)|\[?system\]?\s*:|admin(istrator)?\s+mode|developer\s+mode|sudo)\b", re.I),
    "role_change": re.compile(r"\b(you are now|from now on you|act as (an? )?(admin|system|developer)|pretend (to be|you are))\b", re.I),
    "dictates_output": re.compile(r"\b(classify|mark|label|rate|set)\b[^.\n]{0,40}\b(as|to)\b[^.\n]{0,30}\b(not suspicious|low urgency|confidence\s*(of\s*)?1(\.0)?|auto[_ -]?refund)\b", re.I),
    "skips_review": re.compile(r"\b(without|skip|bypass)\b[^.\n]{0,25}\b(human|manual|review|approval|policy|guardrails?|verification)\b", re.I),
    "reveals_prompt": re.compile(r"\b(reveal|show|print|repeat|output)\b[^.\n]{0,30}\b(system prompt|your (instructions|prompt|rules))\b", re.I),
    "tag_breakout": re.compile(r"</?\s*ticket\s*>", re.I),
    "other_destination": re.compile(r"\b(refund|send|transfer|wire|pay)\b[^.\n]{0,60}\b(to|onto)\b[^.\n]{0,25}\b(different|another|new|other|not the)\b[^.\n]{0,15}\b(card|account|bank|wallet|iban)\b", re.I),
}


@dataclass
class Manipulation:
    signals: list[str] = field(default_factory=list)

    @property
    def detected(self) -> bool:
        return bool(self.signals)


def detect_manipulation(*texts: str) -> Manipulation:
    """Regex tripwires for common prompt-injection and refund-fraud phrasing.

    These are deliberately blunt: a false positive only sends the ticket to a human, while a
    miss falls back on the triage model and the policy rules.
    """
    blob = "\n".join(texts)
    return Manipulation([name for name, pattern in _SIGNALS.items() if pattern.search(blob)])
