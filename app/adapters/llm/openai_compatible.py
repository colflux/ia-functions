"""Proveedor genérico para cualquier API compatible con OpenAI.

Groq, Cerebras y Gemini hablan el mismo protocolo, así que cambiar de uno a otro
es cuestión de la URL, la clave y el nombre del modelo. Todo lo demás se
comparte: temperatura cero, omitir las herramientas cuando no hay ninguna que
declarar y las trazas de coste por llamada.

Sin reintentos automáticos: la librería de OpenAI, ante un 429, esperaba en
silencio lo que pidiera el proveedor (hasta un minuto, dos veces) y la persona
veía el chat pensando uno o dos minutos. Ahora cualquier rechazo del proveedor
se convierte en ProveedorNoDisponible y quien lo usa decide: pasar a otro
proveedor o avisar."""

import json
import logging

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from app.adapters.llm._openai_compat import (
    parse_openai_tool_calls,
    to_openai_messages,
    to_openai_tools,
    to_reply,
)
from app.domain.models import Message, ModelReply, ToolSpec
from app.domain.ports.llm_provider import LLMProvider, ProveedorNoDisponible

logger = logging.getLogger("uvicorn.error")

TIEMPO_MAXIMO = 45  # segundos por llamada; Nginx corta a los 120 y puede haber varias vueltas
PAUSA_CUOTA = 20    # segundos sin usar un proveedor tras un 429 que no dice cuánto esperar


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: str, model: str, nombre: str = "") -> None:
        self._client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0, timeout=TIEMPO_MAXIMO)
        self._model = model
        self.nombre = nombre or model

    def converse(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        system: str | None = None,
    ) -> ModelReply:
        msgs = self._preparar(to_openai_messages(messages, system))
        tls = to_openai_tools(tools) if tools else None
        # Hay proveedores que rechazan tools y tool_choice en nulo, así que cuando no hay
        # herramientas que declarar se omiten los dos parámetros.
        extra = {"tools": tls, "tool_choice": "auto"} if tls else {}
        n_sys = len(system or "")
        n_msgs = len(json.dumps(msgs, ensure_ascii=False))
        n_tls = len(json.dumps(tls, ensure_ascii=False)) if tls else 0
        logger.info("LLM %s | sistema ~%d | historial ~%d | herramientas ~%d | TOTAL ~%d tokens", self.nombre,
                    n_sys // 4, (n_msgs - n_sys) // 4, n_tls // 4, (n_msgs + n_tls) // 4)
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                # Temperatura 0: para consultar datos científicos interesa que la
                # misma pregunta siga siempre el mismo razonamiento, no variedad.
                temperature=0,
                messages=msgs,
                **extra,
            )
        except APIStatusError as exc:
            # Cualquier rechazo del proveedor pasa al siguiente: 429 cuota, 402 sin
            # crédito, 5xx caído, y también 400, porque cada proveedor tiene sus
            # propias exigencias de formato (Groq a veces no logra armar la llamada
            # a una herramienta; Gemini pide firmas propias) y otro puede atenderla.
            espera = 0.0
            if exc.status_code == 429:
                try:
                    espera = float(exc.response.headers.get("retry-after") or PAUSA_CUOTA)
                except (TypeError, ValueError):
                    espera = PAUSA_CUOTA
            nivel = logging.WARNING if exc.status_code in (402, 413, 429) or exc.status_code >= 500 else logging.ERROR
            logger.log(nivel, "LLM %s rechazó la llamada: HTTP %s %s", self.nombre, exc.status_code, str(exc)[:300])
            raise ProveedorNoDisponible(f"{self.nombre}: HTTP {exc.status_code}", espera) from exc
        except (APIConnectionError, APITimeoutError) as exc:
            raise ProveedorNoDisponible(f"{self.nombre}: {type(exc).__name__}") from exc
        message = response.choices[0].message
        raw_tool_calls = [
            {"id": tc.id, "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in (message.tool_calls or [])
        ]
        self._recibido(message)
        return to_reply(message.content, parse_openai_tool_calls(raw_tool_calls))

    def _preparar(self, msgs: list[dict]) -> list[dict]:
        """Ajustes propios de un proveedor sobre los mensajes antes de enviarlos."""
        return msgs

    def _recibido(self, message) -> None:
        """Lo que un proveedor necesite guardar de su respuesta."""
