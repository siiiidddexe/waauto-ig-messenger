import logging

logger = logging.getLogger(__name__)


def get_ai_reply(message_text: str, agent_config: dict) -> str | None:
    """
    Generate a reply using Google Gemini.
    Returns the reply string or None on failure.
    """
    try:
        import google.generativeai as genai

        api_key = agent_config.get("gemini_api_key", "").strip()
        if not api_key:
            logger.warning("Gemini API key not configured.")
            return None

        genai.configure(api_key=api_key)

        model_name = agent_config.get("gemini_model", "gemini-1.5-flash")
        system_prompt = agent_config.get(
            "system_prompt", "You are a helpful support assistant. Be friendly and concise."
        )

        model = genai.GenerativeModel(
            model_name, system_instruction=system_prompt
        )
        response = model.generate_content(message_text)
        return response.text.strip()

    except Exception as exc:
        logger.error("Gemini error: %s", exc)
        return None


def should_trigger(message_text: str, agent_config: dict) -> bool:
    """Check whether the AI agent should respond to this message."""
    if not agent_config.get("enabled"):
        return False
    wakeword = (agent_config.get("wakeword") or "").strip().lower()
    if not wakeword:
        return True  # always-on mode
    return wakeword in message_text.lower()
