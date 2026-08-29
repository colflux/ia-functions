from functools import lru_cache

from app.config import settings
from app.llm.base import LLMProvider


@lru_cache
def get_llm_provider() -> LLMProvider:
    provider = settings.llm_provider.lower()

    if provider == "groq":
        from app.llm.groq_provider import GroqProvider

        return GroqProvider()
    if provider == "gemini":
        from app.llm.gemini_provider import GeminiProvider

        return GeminiProvider()
    if provider == "ollama":
        from app.llm.ollama_provider import OllamaProvider

        return OllamaProvider()
    if provider == "anthropic":
        from app.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider()

    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider}")
