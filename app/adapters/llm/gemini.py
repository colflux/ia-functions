"""Gemini por su API compatible con OpenAI, con el mismo adaptador que Groq y
Cerebras. Reemplaza al adaptador anterior sobre google-genai, que apuntaba a
gemini-1.5-flash (retirado) y no manejaba cuotas.

Los modelos Gemini 3 exigen que cada llamada a herramienta que vuelve en el
historial traiga la «firma de pensamiento» con la que Gemini la generó; sin
ella responden 400. Las llamadas propias se reenvían con su firma real. Las que
hizo otro proveedor (Gemini entra como respaldo a mitad de una pregunta) o el
orquestador (diccionario anticipado) no tienen firma: van con el valor que
Google documenta para historiales que vienen de otro modelo."""

from collections import OrderedDict

from app.adapters.llm.openai_compatible import OpenAICompatibleProvider
from app.config import settings

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
FIRMA_AJENA = "skip_thought_signature_validator"
MAX_FIRMAS = 500


class GeminiProvider(OpenAICompatibleProvider):
    def __init__(self) -> None:
        super().__init__(settings.gemini_api_key, BASE_URL, settings.gemini_model, "gemini")
        self._firmas: OrderedDict[str, str] = OrderedDict()

    def _recibido(self, message) -> None:
        for tc in message.tool_calls or []:
            extra = (getattr(tc, "model_extra", None) or {}).get("extra_content") or {}
            firma = (extra.get("google") or {}).get("thought_signature")
            if firma:
                self._firmas[tc.id] = firma
                while len(self._firmas) > MAX_FIRMAS:
                    self._firmas.popitem(last=False)

    def _preparar(self, msgs: list[dict]) -> list[dict]:
        for m in msgs:
            for tc in m.get("tool_calls") or []:
                firma = self._firmas.get(tc["id"], FIRMA_AJENA)
                tc["extra_content"] = {"google": {"thought_signature": firma}}
        return msgs
