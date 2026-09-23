"""Proveedor genérico para cualquier API compatible con OpenAI.

Groq y Cerebras hablan el mismo protocolo, así que cambiar de uno a otro es
cuestión de la URL, la clave y el nombre del modelo. Todo lo demás se comparte:
temperatura cero, omitir las herramientas cuando no hay ninguna que declarar,
las trazas de coste por llamada y la traducción de los errores de cuota."""

import json
import logging
import re

from openai import APIStatusError, OpenAI

from app.adapters.llm._openai_compat import (
    parse_openai_tool_calls,
    to_openai_messages,
    to_openai_tools,
    to_reply,
)
from app.domain.models import Message, ModelReply, ToolSpec
from app.domain.ports.llm_provider import LLMProvider

logger = logging.getLogger("uvicorn.error")

# Los planes gratuitos suelen tener dos techos: tokens por minuto y tokens por día.
# Sin capturar el error, la excepción sube hasta FastAPI y sale un 500 sin
# cabeceras CORS: el navegador muestra un error de red y no se entiende qué pasó.
# Y conviene distinguir los dos, porque ante la cuota diaria agotada no sirve de
# nada acortar la pregunta.
LIMITE_EXCEDIDO = (
    "La consulta superó el límite de tokens por minuto del plan actual del proveedor "
    "del modelo. Prueba con una pregunta más corta o más concreta, o empieza una "
    "conversación nueva para soltar el historial acumulado."
)

SIN_CREDITO = (
    "El proveedor del modelo rechaza las consultas por falta de crédito o de un "
    "plan activo. Hay que revisarlo en su panel de facturación."
)

LIMITE_DIARIO = (
    "Se agotó la cuota diaria de tokens del plan gratuito del proveedor del modelo. "
    "No depende de la pregunta: hasta que se reponga, ninguna consulta va a funcionar."
)


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    def converse(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        system: str | None = None,
    ) -> ModelReply:
        msgs = to_openai_messages(messages, system)
        tls = to_openai_tools(tools) if tools else None
        # Hay proveedores que rechazan tools y tool_choice en nulo, así que cuando no hay
        # herramientas que declarar se omiten los dos parámetros.
        extra = {"tools": tls, "tool_choice": "auto"} if tls else {}
        n_sys = len(system or "")
        n_msgs = len(json.dumps(msgs, ensure_ascii=False))
        n_tls = len(json.dumps(tls, ensure_ascii=False)) if tls else 0
        logger.info("LLM | sistema ~%d | historial ~%d | herramientas ~%d | TOTAL ~%d tokens", n_sys // 4, (n_msgs - n_sys) // 4, n_tls // 4, (n_msgs + n_tls) // 4)
        if tls:
            logger.debug("LLM detalle: %s", {t["function"]["name"]: len(json.dumps(t, ensure_ascii=False)) // 4 for t in tls})
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
            if exc.status_code == 402:
                logger.warning("LLM 402 sin credito: %s", str(exc)[:300])
                return to_reply(SIN_CREDITO, [])
            if exc.status_code in (413, 429):
                detalle = str(exc)
                logger.warning("LLM limite %s: %s", exc.status_code, detalle[:500])
                if "per day" in detalle or "TPD" in detalle:
                    m = re.search(r"try again in ([0-9hms.]+)", detalle)
                    espera = " Se repone en " + m.group(1).rstrip(".") + "." if m else ""
                    return to_reply(LIMITE_DIARIO + espera, [])
                return to_reply(LIMITE_EXCEDIDO, [])
            raise
        message = response.choices[0].message
        raw_tool_calls = [
            {"id": tc.id, "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in (message.tool_calls or [])
        ]
        return to_reply(message.content, parse_openai_tool_calls(raw_tool_calls))
