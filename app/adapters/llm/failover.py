"""Varios proveedores en cadena: si el primero no puede atender (cuota por
minuto agotada, sin crédito, caído, o rechaza la petición), se pasa al
siguiente en la misma llamada.

Existe porque el plan gratuito de Cerebras acepta unas 5 peticiones por minuto
y cada pregunta hace dos o tres: con dos personas a la vez ya se llenaba.
Sumando los planes gratuitos de Groq y Gemini la capacidad se multiplica, y
cuando ninguno puede se avisa en vez de dejar a la persona esperando.

Un proveedor que respondió 429 queda en pausa los segundos que pidió (o 20):
así no se gasta una llamada sabiendo que la va a rechazar."""

import logging
import threading
import time

from app.domain.models import Message, ModelReply, ToolSpec
from app.domain.ports.llm_provider import LLMProvider, ProveedorNoDisponible

logger = logging.getLogger("uvicorn.error")

SATURADO = ("En este momento hay muchas consultas y el servicio del modelo está saturado. "
            "Intenta de nuevo en un minuto.")
MAX_PAUSA = 120  # segundos; un Retry-After mayor (cuota diaria) no deja fuera al proveedor por horas


class FailoverProvider(LLMProvider):
    def __init__(self, proveedores: list[LLMProvider]) -> None:
        if not proveedores:
            raise ValueError("FailoverProvider necesita al menos un proveedor")
        self._proveedores = proveedores
        self._pausa_hasta: dict[int, float] = {}
        self._candado = threading.Lock()

    def converse(self, messages: list[Message], tools: list[ToolSpec], system: str | None = None) -> ModelReply:
        ahora = time.monotonic()
        with self._candado:
            libres = [p for i, p in enumerate(self._proveedores) if self._pausa_hasta.get(i, 0) <= ahora]
        # Si todos están en pausa se intenta igual con todos: la pausa es una estimación.
        for proveedor in libres or self._proveedores:
            try:
                return proveedor.converse(messages, tools, system)
            except ProveedorNoDisponible as exc:
                logger.warning("LLM no disponible, se pasa al siguiente: %s", exc)
                if exc.espera:
                    with self._candado:
                        self._pausa_hasta[self._proveedores.index(proveedor)] = (
                            time.monotonic() + min(exc.espera, MAX_PAUSA))
        logger.warning("LLM ningún proveedor pudo responder")
        return ModelReply(text=SATURADO)
