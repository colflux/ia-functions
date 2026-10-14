"""Decide si un mensaje de un grupo de observación es una descripción de lo que
se ve o se percibe en la foto o el sitio. Lo demás (saludos, risas, emojis,
coordinación del grupo, «puedes describir la imagen») no se guarda.

Ante la duda se guarda: perder una descripción real es peor que guardar un
mensaje de más, que además se puede borrar después. Si el modelo no responde,
también se guarda.
"""
import json
import logging
import re
import unicodedata

from app.domain.models import Message
from app.domain.ports.llm_provider import LLMProvider

logger = logging.getLogger("uvicorn.error")

PROMPT = """Eres un filtro para un estudio de percepción del paisaje. En un grupo de chat se envían fotos de un sitio natural (páramo, humedal, sabana) y las personas describen lo que ven o perciben.

Te paso un mensaje del grupo. Decide si es una DESCRIPCIÓN: dice algo sobre lo que se ve, se siente o se percibe del sitio o de la foto (vegetación, agua, suelo, clima, temperatura, luz, colores, olores, sonidos, animales, estado del terreno, sensaciones o comparaciones sobre el lugar), aunque sea corta, informal o tenga errores.

NO es descripción: saludos, risas, emojis, agradecimientos, nombres sueltos, bromas, instrucciones o preguntas a otros («puedes describir la imagen», «manden audios»), comentarios sobre la prueba o el chat, y cualquier tema ajeno al sitio.

Si dudas, responde que sí es descripción.

Responde solo con JSON: {"descripcion": true} o {"descripcion": false}"""

_RISAS = re.compile(r"^(?:[jh]+[aeiou]*)+$|^(?:lo+l|x+d+|ok+|si+|no+|ya+|gracias)$")


def _plano(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()


def sin_contenido(texto: str) -> bool:
    """Mensajes que se descartan sin preguntar al modelo: sin letras (emojis,
    signos) o solo risas o un «ok»."""
    letras = re.sub(r"[^a-z]", "", _plano(texto))
    return not letras or bool(_RISAS.fullmatch(letras))


class FiltroDescripciones:
    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    def es_descripcion(self, texto: str) -> bool:
        texto = (texto or "").strip()
        if sin_contenido(texto):
            return False
        try:
            respuesta = self._llm.converse([Message(role="user", text=f"Mensaje: {texto[:1500]}")], [], system=PROMPT)
            valor = json.loads(re.search(r"\{.*\}", respuesta.text, re.DOTALL).group(0)).get("descripcion")
        except Exception:
            logger.warning("GRUPO no se pudo filtrar el mensaje; se guarda", exc_info=True)
            return True
        logger.info("GRUPO filtro %r → descripcion=%s", texto[:60], valor)
        return valor is not False
