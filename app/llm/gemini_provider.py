import google.generativeai as genai

from app.config import settings
from app.llm.base import LLMProvider


class GeminiProvider(LLMProvider):
    def __init__(self) -> None:
        genai.configure(api_key=settings.gemini_api_key)
        self._model_name = settings.gemini_model

    def generate(self, prompt: str, system: str | None = None) -> str:
        model = genai.GenerativeModel(
            self._model_name,
            system_instruction=system,
        )
        response = model.generate_content(prompt)
        return response.text or ""
