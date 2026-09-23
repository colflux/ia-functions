"""El bucle de tool-calling: le da al modelo una lista cerrada de
herramientas (locales y remotas, vía el ToolRegistry), ejecuta las que
decida usar, y deja que redacte la respuesta final con esos resultados.
El modelo nunca ejecuta nada directamente.

Reemplaza (sin sus bugs) al `responder()` que existía en el módulo de IA
del backend: la variable de "pendiente de confirmar" siempre está definida,
la clave de resultado es consistente entre el orquestador y quien la
consume, y el schema de argumentos de cada tool sí llega completo al
modelo."""

import json
import logging
import re

from app.domain.models import Message, PendingConfirmation, ToolCall
from app.domain.ports.conversation_repository import ConversationRepository
from app.domain.ports.llm_provider import LLMProvider
from app.domain.services.bienvenida import respuesta_fija
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
    "Cuando cites un dato, menciona su fuente. "
    "No inventes secciones, menús, botones, niveles de acceso ni funciones de la "
    "plataforma: si algo no sale de tus herramientas o documentos, di que no tienes "
    "esa información. "
    "Los archivos que las personas suben desde el chat se consultan con "
    "consultar_archivos_subidos, también cuando piden descargarlos. Si las mediciones "
    "de la plataforma no tienen el dato, revisa esos archivos antes de decir que no "
    "hay; si el dato sale de ahí, dilo y aclara que no ha pasado por la validación "
    "del ETL. "
    "Sé puntual: responde primero y directo lo que se preguntó, en pocas frases "
    "(unas 120 palabras como máximo) salvo que pidan más detalle. No uses tablas ni "
    "marcas de cita, y no agregues conteos ni valores de ejemplo que no se pidieron. "
    "No afirmes relaciones que los datos no muestran: si algo no se puede comprobar "
    "con los datos, dilo en una frase. Las interpretaciones de expresiones de campo "
    "salen del diccionario; si una no está, dilo."
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


def _sin_tablas_ni_citas(texto: str) -> str:
    """Red de seguridad por si el modelo no sigue las reglas de forma: quita las
    marcas de cita (【source: …】) y convierte cada fila de una tabla en una línea
    de texto; el chat muestra texto plano y una tabla se ve como un muro de barras."""
    texto = re.sub(r"【[^】]*】", "", texto)
    lineas = []
    for linea in texto.splitlines():
        celdas = linea.strip()
        if celdas.startswith("|") and celdas.endswith("|"):
            if re.fullmatch(r"\|[\s:|-]+\|", celdas):
                continue  # separador |---|---|
            lineas.append(" — ".join(c.strip() for c in celdas.strip("|").split("|") if c.strip()))
        else:
            lineas.append(linea)
    return re.sub(r"[ \t]+([.,;:])", r"\1", "\n".join(lineas)).strip()


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

        fija = respuesta_fija(text)
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

        for vuelta in range(MAX_TURNS):
            # La primera vuelta va con la seleccion del enrutador, que es donde esta
            # el ahorro. Si hace falta una segunda, la pregunta ya demostro que
            # necesita varios pasos: ahi importa mas no dejar al modelo sin la
            # herramienta que le falta que ahorrar tokens. Quitarlas del todo se
            # probo y se descarto: con el historial lleno de llamadas el modelo
            # imitaba el protocolo en texto plano e inventaba resultados.
            declaradas = visibles if vuelta == 0 else todas
            reply = self._llm.converse(messages, declaradas, SYSTEM_PROMPT)
            if not reply.tool_calls:
                break

            messages.append(Message(role="assistant", text=reply.text, tool_calls=reply.tool_calls))
            encadenadas: list[dict] = []
            for call in reply.tool_calls:
                tools_used.append(call.name)
                result = self._tools.call_tool(call.name, call.arguments)
                logger.info("HERRAMIENTA %s | args=%s | resultado=%s", call.name, call.arguments, str(result)[:400])
                encadenada = result.pop("consultar_tambien", None)
                if encadenada:
                    encadenadas.append(encadenada)

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
            self._encadenar(encadenadas, {t.name for t in todas}, messages, tools_used, sources)

        respaldo = (
            "Encontré información relacionada, pero no alcancé a armar la respuesta. "
            "Pregúntamelo de nuevo de forma más concreta, o por partes."
            if sources else
            "No pude completar la consulta con las herramientas disponibles. "
            "Intenta reformular la pregunta o indicar el sitio por su nombre o número."
        )
        answer = (reply.text if reply else "") or respaldo
        answer = _sin_tablas_ni_citas(_texto_plano(answer))
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

    def _encadenar(self, encadenadas: list[dict], disponibles: set[str], messages: list[Message],
                   tools_used: list[str], sources: list[dict]) -> None:
        """Consultas que una herramienta pide hacer además de la suya (clave
        "consultar_tambien"). Se ejecutan de una vez, sin esperar a que el modelo
        lo decida: así un "no hay mediciones en la plataforma" llega junto con lo
        que haya en los archivos subidos, y el modelo no puede responder que no
        hay datos sin haber mirado (probado: con solo la indicación, le decía a
        la persona que usara la herramienta en vez de usarla)."""
        hechas: set[tuple[str, str]] = set()
        for pedido in encadenadas:
            nombre = pedido.get("herramienta")
            argumentos = pedido.get("argumentos") or {}
            clave = (nombre, json.dumps(argumentos, sort_keys=True))
            if nombre not in disponibles or clave in hechas:
                continue
            hechas.add(clave)
            llamada = ToolCall(name=nombre, arguments=argumentos)
            resultado = self._tools.call_tool(nombre, argumentos)
            logger.info("ENCADENADA %s | args=%s | resultado=%s", nombre, argumentos, str(resultado)[:400])
            tools_used.append(nombre)
            sources.extend(resultado.pop("sources", None) or [])
            messages.append(Message(role="assistant", text="", tool_calls=[llamada]))
            messages.append(Message(role="tool", tool_call_id=llamada.id, tool_name=nombre, tool_result=resultado))

    @staticmethod
    def _dedupe_sources(sources: list[dict]) -> list[dict]:
        unique: dict[tuple, dict] = {}
        for s in sources:
            key = (s["source"], s["content"][:80])
            if key not in unique or s["score"] > unique[key]["score"]:
                unique[key] = s
        return sorted(unique.values(), key=lambda s: s["score"], reverse=True)
