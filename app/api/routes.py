from dataclasses import asdict

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile

from app.api.schemas import (
    CargaResponse, ChatRequest, ChatResponse, HistorialResponse, IngestRequest, IngestResponse,
)
from app.bootstrap.container import (
    get_carga_documentos, get_conversation_repository, get_orchestrator, get_rag_service,
)
from app.domain.services.agent_orchestrator import HISTORY_MINUTES
from app.domain.services.bienvenida import BIENVENIDA
from app.domain.services.carga_documentos import ErrorDeCarga

router = APIRouter()


@router.get("/bienvenida")
def bienvenida() -> dict:
    """Presentación que el chat muestra al abrirse."""
    return {"texto": BIENVENIDA}


@router.post("/ingest", response_model=IngestResponse)
def ingest(payload: IngestRequest) -> IngestResponse:
    count = get_rag_service().ingest_document(payload.source, payload.text, payload.collection)
    return IngestResponse(chunks_indexed=count)


@router.post("/documentos", response_model=CargaResponse)
def subir_documento(
    archivo: UploadFile = File(...),
    descripcion: str = Form(default=""),
    complementos: str = Form(default=""),
    authorization: str | None = Header(default=None),
) -> CargaResponse:
    """Sube un archivo desde el chat. Exige sesión con nivel reportador; el
    modelo revisa el contenido y solo se guarda si tiene relación con COLFLUX y
    coincide con la `descripcion` que dio la persona antes de elegirlo.
    Si es un archivo de datos y le faltan campos obligatorios, responde
    `pendiente` con lo que falta; el chat reenvía el archivo con las
    respuestas de la persona en `complementos` (una por línea)."""
    carga = get_carga_documentos()
    try:
        usuario = carga.verificar_permiso(authorization)
        contenido = archivo.file.read(carga.max_bytes + 1)
        resultado = carga.subir(
            usuario,
            authorization,
            archivo.filename or "archivo",
            contenido,
            archivo.content_type,
            descripcion,
            [linea.strip() for linea in complementos.splitlines() if linea.strip()],
        )
    except ErrorDeCarga as exc:
        raise HTTPException(status_code=exc.estado, detail=exc.mensaje)
    return CargaResponse(**asdict(resultado))


@router.get("/documentos/descargar")
def descargar_documento(archivo: str, authorization: str | None = Header(default=None)) -> dict:
    """Enlace temporal (1 hora) al original de un archivo subido. Solo para
    reportadores y administradores: el nivel se verifica con el backend."""
    carga = get_carga_documentos()
    try:
        carga.verificar_permiso(authorization)
        return {"url": carga.enlace_descarga(archivo)}
    except ErrorDeCarga as exc:
        raise HTTPException(status_code=exc.estado, detail=exc.mensaje)


@router.get("/documentos/imagen")
def ver_imagen(archivo: str) -> dict:
    """Enlace temporal (1 hora) para mostrar en el chat una imagen subida.
    Abierto a cualquiera: las imágenes son contenido que la plataforma muestra."""
    try:
        return {"url": get_carga_documentos().enlace_imagen(archivo)}
    except ErrorDeCarga as exc:
        raise HTTPException(status_code=exc.estado, detail=exc.mensaje)


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
