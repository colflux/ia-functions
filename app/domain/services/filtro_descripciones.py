"""Lee un mensaje de un grupo de observación y decide dos cosas en una sola
llamada al modelo:
1. si es una descripción de lo que se ve o se percibe en la foto o el sitio
   (lo demás —saludos, risas, emojis, coordinación del grupo, «puedes describir
   la imagen»— no se guarda);
2. qué atributos del sitio menciona (ver atributos_sitio.py), solo los que dice.

En el chat privado el bot hace preguntas sobre la foto (preguntas_foto.py): ahí
el mensaje se lee junto con la pregunta, porque «alto» o «no» solo se entienden
como respuesta a «¿cómo ves el nivel del agua?» o «¿ves animales?».

Ante la duda se guarda: perder una descripción real es peor que guardar un
mensaje de más, que se puede borrar después. Si el modelo no responde, también
se guarda, sin atributos (se pueden completar después).
"""
import json
import logging
import re
import unicodedata
from typing import Any

from app.domain.models import Message
from app.domain.ports.llm_provider import LLMProvider
from app.domain.services.atributos_sitio import esquema_para_prompt, limpiar

logger = logging.getLogger("uvicorn.error")

PROMPT = """Eres un asistente de un estudio de percepción del paisaje. En un grupo de chat se envían fotos de un sitio natural (páramo, humedal, sabana inundable, morichal) y las personas describen lo que ven o perciben.

Te paso un mensaje del grupo. Haz dos cosas:

1. "descripcion": true si el mensaje dice algo sobre lo que se ve, se siente o se percibe del sitio o de la foto (vegetación, agua, suelo, cielo, temperatura, luz, colores, olores, sonidos, animales, estado del terreno, sensaciones o comparaciones sobre el lugar), aunque sea corto, informal o tenga errores. false si son saludos, risas, emojis, agradecimientos, nombres sueltos, bromas, instrucciones o preguntas a otros («puedes describir la imagen»), comentarios sobre la prueba o el chat, o temas ajenos al sitio. Si dudas, true.

2. "atributos": lo que el mensaje DICE del sitio, con estos campos:
{esquema}
- "certeza": "percibe" si lo afirma como algo que ve o siente; "supone" si lo deduce o duda («parece que llovió», «debe hacer frío»).
Usa solo lo que el mensaje dice; no deduzcas ni completes. Un campo que no se menciona va en null. Si no es descripción, atributos va en null.

Si además te paso la pregunta que se le hizo, el mensaje es su respuesta: "descripcion" es true si responde a la pregunta, aunque hable del territorio, de su experiencia, de su comunidad, de cómo era antes o de las épocas del año, y aunque sea corta o negativa («alto», «verdes y secas», «no hay agua», «no veo animales», «no»). false solo si no tiene que ver con la pregunta (saludos, bromas, otros temas). Los atributos salen solo de lo que dice la respuesta, nunca de la pregunta.

Responde solo con JSON: {{"descripcion": true, "atributos": {{...}}}}"""

_RISAS = re.compile(r"^(?:[jh]+[aeiou]*)+$|^(?:lo+l|x+d+|ok+|si+|no+|ya+|gracias)$")
_SI_NO = re.compile(r"^(?:si+|no+)$")


def _plano(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()


def sin_contenido(texto: str, respuesta: bool = False) -> bool:
    """Mensajes que se descartan sin preguntar al modelo: sin letras (emojis,
    signos) o solo risas o un «ok». Si responde a una pregunta (`respuesta`),
    «sí» y «no» sí dicen algo."""
    letras = re.sub(r"[^a-z]", "", _plano(texto))
    if respuesta and _SI_NO.fullmatch(letras):
        return False
    return not letras or bool(_RISAS.fullmatch(letras))


class FiltroDescripciones:
    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm
        self._prompt = PROMPT.format(esquema=esquema_para_prompt())

    def analizar(self, texto: str, pregunta: str | None = None) -> tuple[bool, dict[str, Any] | None]:
        """(es una descripción, atributos que menciona). Con `pregunta`, el
        mensaje es la respuesta a esa pregunta sobre la foto."""
        texto = (texto or "").strip()
        if sin_contenido(texto, respuesta=bool(pregunta)):
            return False, None
        entrada = (f"Pregunta que se le hizo sobre la foto: {pregunta[:500]}\nMensaje (su respuesta): {texto[:1500]}"
                   if pregunta else f"Mensaje: {texto[:1500]}")
        try:
            respuesta = self._llm.converse([Message(role="user", text=entrada)], [], system=self._prompt)
            datos = json.loads(re.search(r"\{.*\}", respuesta.text, re.DOTALL).group(0))
        except Exception:
            logger.warning("GRUPO no se pudo analizar el mensaje; se guarda sin atributos", exc_info=True)
            return True, None
        es = datos.get("descripcion") is not False
        atributos = limpiar(datos.get("atributos")) if es else None
        logger.info("GRUPO filtro %r → descripcion=%s atributos=%s", texto[:60], es, atributos)
        return es, atributos

    def es_descripcion(self, texto: str) -> bool:
        return self.analizar(texto)[0]
