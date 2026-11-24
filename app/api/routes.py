from fastapi import APIRouter

from app.api.schemas import (
    ChatRequest, ChatResponse, HistorialResponse, IngestRequest, IngestResponse,
)
from app.bootstrap.container import get_conversation_repository, get_orchestrator, get_rag_service
from app.domain.services.agent_orchestrator import HISTORY_MINUTES

router = APIRouter()


@router.post("/ingest", response_model=IngestResponse)
def ingest(payload: IngestRequest) -> IngestResponse:
    count = get_rag_service().ingest_document(payload.source, payload.text, payload.collection)
    return IngestResponse(chunks_indexed=count)


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    result = get_orchestrator().respond(payload.message, payload.usuario, token=payload.token)
    return ChatResponse(
        answer=result["answer"],
        sources=result["sources"],
        herramientas=result["tools_used"],
        pendiente_de_confirmacion=result["pending_confirmation"],
    )


@router.get("/chat/historial", response_model=HistorialResponse)
def historial(usuario: str, limite: int = 40) -> HistorialResponse:
    messages = get_conversation_repository().recent_messages(usuario, min(limite, 100), HISTORY_MINUTES)
    return HistorialResponse(messages=[{"role": m.role, "content": m.text} for m in messages])
