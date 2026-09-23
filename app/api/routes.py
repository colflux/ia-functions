from dataclasses import asdict

from fastapi import APIRouter, File, Header, HTTPException, UploadFile

from app.api.schemas import (
    CargaResponse, ChatRequest, ChatResponse, HistorialResponse, IngestRequest, IngestResponse,
)
from app.bootstrap.container import (
    get_carga_documentos, get_conversation_repository, get_orchestrator, get_rag_service,
)
from app.domain.services.agent_orchestrator import HISTORY_MINUTES
from app.domain.services.carga_documentos import ErrorDeCarga

router = APIRouter()


@router.post("/ingest", response_model=IngestResponse)
def ingest(payload: IngestRequest) -> IngestResponse:
    count = get_rag_service().ingest_document(payload.source, payload.text, payload.collection)
    return IngestResponse(chunks_indexed=count)


@router.post("/documentos", response_model=CargaResponse)
def subir_documento(
    archivo: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> CargaResponse:
    """Sube un archivo desde el chat. Exige sesión con nivel reportador; el
    modelo revisa el contenido y solo se guarda si tiene relación con COLFLUX."""
    carga = get_carga_documentos()
    try:
        usuario = carga.verificar_permiso(authorization)
        contenido = archivo.file.read(carga.max_bytes + 1)
        resultado = carga.subir(usuario, archivo.filename or "archivo", contenido, archivo.content_type)
    except ErrorDeCarga as exc:
        raise HTTPException(status_code=exc.estado, detail=exc.mensaje)
    return CargaResponse(**asdict(resultado))


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    result = get_orchestrator().respond(payload.message, payload.usuario)
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
