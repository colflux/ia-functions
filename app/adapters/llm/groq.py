"""Groq: gpt-oss-120b sobre su API compatible con OpenAI."""

from app.adapters.llm.openai_compatible import OpenAICompatibleProvider
from app.config import settings

BASE_URL = "https://api.groq.com/openai/v1"


class GroqProvider(OpenAICompatibleProvider):
    def __init__(self) -> None:
        super().__init__(settings.groq_api_key, BASE_URL, settings.groq_model)
