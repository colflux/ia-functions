"""Elige qué herramientas se le declaran al modelo en cada pregunta.

Las declaraciones viajan en TODAS las llamadas, se usen o no: medido, 1.329
tokens por llamada con siete herramientas, frente a 225 del prompt del sistema.
Comparar la pregunta con la descripción de cada herramienta usando el modelo de
embeddings que ya corre aquí no cuesta nada y permite declarar solo las que
vienen a cuento.

Si algo falla se declaran todas: gastar tokens de más es preferible a dejar al
asistente sin la herramienta que necesitaba."""

import logging
import math

from app.domain.models import ToolSpec
from app.domain.ports.embedding_provider import EmbeddingProvider

logger = logging.getLogger("uvicorn.error")


def _coseno(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return num / (na * nb) if na and nb else 0.0


class ToolRouter:
    def __init__(self, embeddings: EmbeddingProvider, cuantas: int = 3) -> None:
        self._embeddings = embeddings
        self._cuantas = cuantas
        self._cache: dict[str, list[float]] = {}

    def elegir(self, pregunta: str, tools: list[ToolSpec]) -> list[ToolSpec]:
        """Las herramientas más cercanas a la pregunta. Ante la duda, todas."""
        try:
            if len(tools) <= self._cuantas:
                return tools
            faltan = [t for t in tools if t.name not in self._cache]
            if faltan:
                textos = [t.name + ". " + (t.description or "") for t in faltan]
                for t, v in zip(faltan, self._embeddings.embed(textos, "passage")):
                    self._cache[t.name] = v
            consulta = self._embeddings.embed([pregunta], "query")[0]
            orden = sorted(tools, key=lambda t: -_coseno(consulta, self._cache[t.name]))
            elegidas = orden[: self._cuantas]
            logger.info("ROUTER %s -> %s", pregunta[:50], [t.name for t in elegidas])
            return elegidas
        except Exception as exc:
            logger.warning("ROUTER sin filtrar (%s)", exc)
            return tools
