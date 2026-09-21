"""El bucle de tool-calling: le da al modelo una lista cerrada de
herramientas (locales y remotas, vía el ToolRegistry), ejecuta las que
decida usar, y deja que redacte la respuesta final con esos resultados.
El modelo nunca ejecuta nada directamente.

Reemplaza (sin sus bugs) al `responder()` que existía en el módulo de IA
del backend: la variable de "pendiente de confirmar" siempre está definida,
la clave de resultado es consistente entre el orquestador y quien la
consume, y el schema de argumentos de cada tool sí llega completo al
modelo."""

from app.domain.models import Message, PendingConfirmation
from app.domain.ports.conversation_repository import ConversationRepository
from app.domain.ports.llm_provider import LLMProvider
from app.domain.services.tool_registry import ToolRegistry

MAX_TURNS = 5
HISTORY_TURNS = 6
HISTORY_MINUTES = 30

CONFIRMATIONS = {"confirmo", "confirmar", "si confirmo", "sí confirmo", "sí, confirmo"}

SYSTEM_PROMPT = (
    "Eres el asistente de colflux, una plataforma de monitoreo de flujos de "
    "gases de efecto invernadero en ecosistemas colombianos. Responde en "
    "español, breve y concreto, en texto plano (sin LaTeX ni asteriscos). "
    "Si tienes herramientas disponibles y la pregunta las necesita, úsalas "
    "antes de responder; si no hay ninguna relevante o la pregunta es "
    "general (saludos, conversación), responde con naturalidad. Nunca "
    "inventes cifras: si una herramienta no encuentra datos, dilo tal cual. "
    "Cuando cites un dato, menciona su fuente."
)


class AgentOrchestrator:
    def __init__(
        self,
        llm: LLMProvider,
        tools: ToolRegistry,
        conversations: ConversationRepository,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._conversations = conversations

    def respond(self, question: str, external_user_id: str) -> dict:
        text = (question or "").strip()

        if text.lower().rstrip(".!") in CONFIRMATIONS:
            return self._resolve_confirmation(external_user_id)

        history = self._conversations.recent_messages(external_user_id, HISTORY_TURNS, HISTORY_MINUTES)
        messages = [*history, Message(role="user", text=text)]

        tools_used: list[str] = []
        sources: list[dict] = []
        pending: PendingConfirmation | None = None
        reply = None

        for _ in range(MAX_TURNS):
            reply = self._llm.converse(messages, self._tools.list_tools(), SYSTEM_PROMPT)
            if not reply.tool_calls:
                break

            messages.append(Message(role="assistant", text=reply.text, tool_calls=reply.tool_calls))
            for call in reply.tool_calls:
                tools_used.append(call.name)
                result = self._tools.call_tool(call.name, call.arguments)

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

        answer = (reply.text if reply else "") or (
            "No pude completar la consulta con las herramientas disponibles. "
            "Intenta reformular la pregunta o indicar el sitio por su nombre o número."
        )
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
