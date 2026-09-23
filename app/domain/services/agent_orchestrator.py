"""El bucle de tool-calling: le da al modelo una lista cerrada de
herramientas (locales y remotas, vía el ToolRegistry), ejecuta las que
decida usar, y deja que redacte la respuesta final con esos resultados.
El modelo nunca ejecuta nada directamente.

Reemplaza (sin sus bugs) al `responder()` que existía en el módulo de IA
del backend: la variable de "pendiente de confirmar" siempre está definida,
la clave de resultado es consistente entre el orquestador y quien la
consume, y el schema de argumentos de cada tool sí llega completo al
modelo."""

import logging
import unicodedata
import re

from app.domain.models import Message, PendingConfirmation
from app.domain.ports.conversation_repository import ConversationRepository
from app.domain.ports.llm_provider import LLMProvider
from app.domain.services.tool_registry import ToolRegistry

logger = logging.getLogger("uvicorn.error")

MAX_TURNS = 7
HISTORY_TURNS = 6
HISTORY_MINUTES = 30
HISTORY_MAX_CHARS = 6000

CONFIRMATIONS = {"confirmo", "confirmar", "si confirmo", "sí confirmo", "sí, confirmo"}

SYSTEM_PROMPT = (
    "Eres el asistente de COLFLUX, un proyecto de investigación sobre las "
    "dinámicas del carbono en los ecosistemas colombianos NO forestales: páramos, "
    "humedales, sabanas inundables, morichales y sus suelos orgánicos. "
    "Responde en español, breve y concreto, en texto plano, sin LaTeX ni asteriscos. "
    "Usa las herramientas cuando la pregunta necesite datos; no respondas de memoria. "
    "Si la persona describe algo con palabras de campo (olores, colores, texturas, "
    "frases como que el suelo respira fuerte o que el humedal está hirviendo), busca "
    "primero en el diccionario qué significa y después consulta las mediciones. "
    "Nunca inventes cifras ni unidades: si una herramienta no declara la unidad, di "
    "que no está declarada. "
    "No calcules promedios, sumas ni totales por tu cuenta sobre filas parciales: di "
    "cuántos registros hay en total y aclara que lo mostrado es una muestra. "
    "Cuando cites un dato, menciona su fuente."
)



def _acotar_historial(mensajes: list[Message], tope: int) -> list[Message]:
    """Deja los mensajes más recientes que quepan en tope caracteres. Acotar por
    tamaño y no solo por número de turnos es lo que evita que una sola respuesta
    larga (un listado de sitios, por ejemplo) arrastre el límite de tokens del
    proveedor en las preguntas siguientes."""
    acumulado = 0
    recortado: list[Message] = []
    for m in reversed(mensajes):
        largo = len(m.text or "")
        if recortado and acumulado + largo > tope:
            break
        acumulado += largo
        recortado.append(m)
    return list(reversed(recortado))


def _texto_plano(texto: str) -> str:
    """Quita el énfasis de Markdown que el modelo añade pese a pedírsele texto
    plano en el prompt. El widget del chat no lo interpreta, así que los
    asteriscos se verían tal cual en pantalla. Las instrucciones de formato son
    de las que peor obedecen los modelos: sale más fiable limpiarlo aquí."""
    limpio = re.sub(r"\*\*(.+?)\*\*", r"\1", texto or "")
    limpio = re.sub(r"(?<!\*)\*(?!\s)([^*\n]+?)\*", r"\1", limpio)
    limpio = re.sub(r"^\s{0,3}#{1,6}\s+", "", limpio, flags=re.MULTILINE)
    return limpio

class AgentOrchestrator:
    def __init__(
        self,
        llm: LLMProvider,
        tools: ToolRegistry,
        conversations: ConversationRepository,
        router=None,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._conversations = conversations
        self._router = router

    def respond(self, question: str, external_user_id: str) -> dict:
        text = (question or "").strip()

        if text.lower().rstrip(".!") in CONFIRMATIONS:
            return self._resolve_confirmation(external_user_id)

        fija = _respuesta_fija(text)
        if fija:
            self._conversations.save_turn(external_user_id, text, fija, [], None)
            return {"answer": fija, "sources": [], "tools_used": [], "pending_confirmation": None}

        history = self._conversations.recent_messages(external_user_id, HISTORY_TURNS, HISTORY_MINUTES)
        messages = [*_acotar_historial(history, HISTORY_MAX_CHARS), Message(role="user", text=text)]

        tools_used: list[str] = []
        sources: list[dict] = []
        pending: PendingConfirmation | None = None
        reply = None

        todas = self._tools.list_tools()
        visibles = self._router.elegir(text, todas) if self._router else todas

        for _ in range(MAX_TURNS):
            # Las herramientas se declaran en TODAS las vueltas. Quitarlas tras
            # la primera ahorraba 680 tokens, pero con el historial lleno de
            # llamadas el modelo imitaba el protocolo en texto plano e inventaba
            # resultados en vez de redactar. No compensa.
            declaradas = visibles
            reply = self._llm.converse(messages, declaradas, SYSTEM_PROMPT)
            if not reply.tool_calls:
                break

            messages.append(Message(role="assistant", text=reply.text, tool_calls=reply.tool_calls))
            for call in reply.tool_calls:
                tools_used.append(call.name)
                result = self._tools.call_tool(call.name, call.arguments)
                logger.info("HERRAMIENTA %s | args=%s | resultado=%s", call.name, call.arguments, str(result)[:400])

                result_sources = result.pop("sources", None)
                if result_sources:
                    sources.extend(result_sources)

                proposal = result.pop("pending_confirmation", None)
                if proposal:
                    pending = PendingConfirmation(id="", tool_name="confirmar_medicion", arguments=proposal)

                messages.append(
                    Message(
                        role="tool",
                        tool_call_id=call.id,
                        tool_name=call.name,
                        tool_result=result,
                    )
                )

        respaldo = (
            "Encontré el significado en el diccionario, pero no llegué a consultar las "
            "mediciones. Pregúntame por partes: primero qué significa y después los "
            "datos de un sitio concreto."
            if sources else
            "No pude completar la consulta con las herramientas disponibles. "
            "Intenta reformular la pregunta o indicar el sitio por su nombre o número."
        )
        answer = (reply.text if reply else "") or respaldo
        answer = _texto_plano(answer)
        unique_sources = self._dedupe_sources(sources)

        if pending:
            self._conversations.save_turn(external_user_id, text, answer, tools_used, pending)
        else:
            self._conversations.save_turn(external_user_id, text, answer, tools_used, None)

        return {
            "answer": answer,
            "sources": unique_sources,
            "tools_used": list(dict.fromkeys(tools_used)),
            "pending_confirmation": pending.arguments if pending else None,
        }

    def _resolve_confirmation(self, external_user_id: str) -> dict:
        pending = self._conversations.latest_pending(external_user_id)
        if not pending:
            answer = "No hay ninguna propuesta pendiente de confirmar."
            self._conversations.save_turn(external_user_id, "confirmo", answer, [], None)
            return {"answer": answer, "sources": [], "tools_used": [], "pending_confirmation": None}

        if pending.tool_name not in {t.name for t in self._tools.list_tools()}:
            # Las herramientas de escritura no se registran mientras el backend no
            # exponga sus endpoints, así que una propuesta guardada antes de eso no
            # se puede completar. Sin esto, el registro devolvía su error interno
            # y el usuario lo leía como si fuera la respuesta.
            answer = ("Esa propuesta necesita una herramienta de escritura que todavía "
                      "no está disponible, porque el backend no expone sus endpoints. "
                      "No se guardó nada.")
            self._conversations.resolve_pending(pending.id)
            self._conversations.save_turn(external_user_id, "confirmo", answer, [], None)
            return {"answer": answer, "sources": [], "tools_used": [], "pending_confirmation": None}

        result = self._tools.call_tool(pending.tool_name, pending.arguments)
        self._conversations.resolve_pending(pending.id)

        answer = result.get("mensaje") or result.get("error", "Listo.")
        self._conversations.save_turn(external_user_id, "confirmo", answer, [pending.tool_name], None)
        return {"answer": answer, "sources": [], "tools_used": [pending.tool_name], "pending_confirmation": None}

    @staticmethod
    def _dedupe_sources(sources: list[dict]) -> list[dict]:
        unique: dict[tuple, dict] = {}
        for s in sources:
            key = (s["source"], s["content"][:80])
            if key not in unique or s["score"] > unique[key]["score"]:
                unique[key] = s
        return sorted(unique.values(), key=lambda s: s["score"], reverse=True)


SALUDOS = {"hola", "holaa", "buenas", "buenosdias", "buenastardes", "buenasnoches",
           "hey", "quetal", "saludos", "holabuenas"}

CAPACIDADES = ("quepuedeshacer", "quesabeshacer", "enquemepuedesayudar",
               "enquepuedesayudar", "quieneres", "paraquesirves",
               "quemepuedesofrecer")

BIENVENIDA = (
    "Soy el asistente de COLFLUX, la plataforma que monitorea gases de efecto invernadero, "
    "carbono y biomasa en ecosistemas no forestales de Colombia: páramos, humedales, "
    "sabanas inundables y morichales.\n\n"
    "Puedo consultar por ti:\n"
    "- Mediciones de flujo de CO2 y CH4, con su fecha, su valor y su unidad, en un sitio, "
    "una vereda, un municipio o un departamento.\n"
    "- Carbono orgánico del suelo, biomasa y materia orgánica muerta.\n"
    "- Variables ambientales: temperatura del suelo y del aire, humedad, presión y nivel "
    "de agua.\n"
    "- Qué sitios de monitoreo existen y dónde están, incluso buscando por coordenada.\n"
    "- Qué significa un término del trabajo de campo, como cuando alguien dice que el "
    "suelo está respirando fuerte.\n\n"
    "Pregúntame por un lugar y un dato, o por un término que no conozcas."
)


def _clave(texto: str) -> str:
    base = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(c for c in base if c.isalnum())


def _respuesta_fija(texto: str) -> str:
    """Saludos y preguntas sobre qué es el asistente: respuesta fija, sin llamar al
    modelo. Cuesta cero tokens, sale siempre completa y evita que el prompt tenga que
    cargar instrucciones de presentación en todas las llamadas."""
    k = _clave(texto)
    if not k:
        return ""
    if k in SALUDOS:
        return BIENVENIDA
    # La frase tiene que ser casi todo el mensaje. Con mucho texto alrededor,
    # que puedes hacer es parte de una pregunta real: no sé qué puedes hacer
    # con los datos de Caldas no es alguien pidiendo la presentación.
    if any(c in k and len(k) <= len(c) + 10 for c in CAPACIDADES):
        return BIENVENIDA
    return ""
