import requests

from app.config import settings
from app.llm.base import LLMProvider


class OllamaProvider(LLMProvider):
    def __init__(self) -> None:
        self._base_url = settings.ollama_base_url
        self._model = settings.ollama_model

    def generate(self, prompt: str, system: str | None = None) -> str:
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        response = requests.post(
            f"{self._base_url}/api/generate",
            json={"model": self._model, "prompt": full_prompt, "stream": False},
            timeout=120,
        )
        response.raise_for_status()
        return response.json().get("response", "")
