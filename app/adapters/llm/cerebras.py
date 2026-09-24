"""Cerebras: el mismo modelo que Groq, con mucho más margen de cuota.

Plan gratuito: 1.000.000 de tokens al día y entre 60.000 y 100.000 por minuto,
frente a los 200.000 y 8.000 de Groq. A cambio, el contexto está topado en 8.192
tokens, que sobra para nuestras llamadas de unos 1.700. Y solo acepta unas
5 peticiones por minuto: por eso va combinado con otros proveedores (failover.py)."""

from app.adapters.llm.openai_compatible import OpenAICompatibleProvider
from app.config import settings

BASE_URL = "https://api.cerebras.ai/v1"


class CerebrasProvider(OpenAICompatibleProvider):
    def __init__(self) -> None:
        super().__init__(settings.cerebras_api_key, BASE_URL, settings.cerebras_model, "cerebras")
