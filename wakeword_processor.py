import re
import logging

logger = logging.getLogger(__name__)


def find_match(message_text: str, wakewords: list) -> dict | None:
    """
    Scan message_text against a list of wakeword dicts.
    Returns the first matching wakeword dict, or None.
    """
    if not message_text:
        return None

    text_lower = message_text.strip().lower()

    for ww in wakewords:
        if not ww.get("enabled", True):
            continue

        phrase = (ww.get("phrase") or "").lower()
        match_type = ww.get("match_type", "contains")

        matched = False
        if match_type == "contains":
            matched = phrase in text_lower
        elif match_type == "exact":
            matched = text_lower == phrase
        elif match_type == "starts_with":
            matched = text_lower.startswith(phrase)
        elif match_type == "regex":
            try:
                matched = bool(re.search(phrase, message_text, re.IGNORECASE))
            except re.error:
                logger.warning("Invalid wakeword regex id=%s phrase=%s", ww.get("id"), phrase)

        if matched:
            return ww

    return None
