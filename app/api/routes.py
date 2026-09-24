from dataclasses import asdict

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile

from app.api.schemas import (
    CargaResponse, ChatRequest, ChatResponse, DescripcionRequest, HistorialResponse, IngestRequest,
    IngestResponse, LugarRequest,
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


@router.post("/documentos/descripcion", response_model=CargaResponse)
def revisar_descripcion(payload: DescripcionRequest, authorization: str | None = Header(default=None)) -> CargaResponse:
    """Primer paso de la subida: lo que la persona dice que va a subir. Si no
    tiene que ver con COLFLUX, `aceptado` es false y el chat no sigue."""
    carga = get_carga_documentos()
    try:
        carga.verificar_permiso(authorization)
        return CargaResponse(**asdict(carga.revisar_descripcion(payload.descripcion)))
    except ErrorDeCarga as exc:
        raise HTTPException(status_code=exc.estado, detail=exc.mensaje)


@router.post("/documentos/lugar", response_model=CargaResponse)
def revisar_lugar(payload: LugarRequest, authorization: str | None = Header(default=None)) -> CargaResponse:
    """Segundo paso: de dónde es el archivo. Devuelve el lugar interpretado o,
    si no se entiende o falta algo, `aceptado` false con lo que falta."""
    carga = get_carga_documentos()
    try:
        carga.verificar_permiso(authorization)
        lugar = carga.revisar_lugar(payload.lugar)
    except ErrorDeCarga as exc:
        if exc.estado == 422:
            return CargaResponse(aceptado=False, mensaje=exc.mensaje)
        raise HTTPException(status_code=exc.estado, detail=exc.mensaje)
    mensaje = f"Lugar: {lugar.resumen()}."
    if lugar.sitio_cercano:
        mensaje += f" El sitio de la plataforma más cercano es {lugar.sitio_cercano['etiqueta']}, a {lugar.sitio_cercano['distancia_km']} km."
    if lugar.avisos:
        mensaje += " " + " ".join(lugar.avisos)
    return CargaResponse(aceptado=True, mensaje=mensaje, lugar=lugar.resumen())


@router.post("/documentos", response_model=CargaResponse)
def subir_documento(
    archivo: UploadFile = File(...),
    descripcion: str = Form(default=""),
    complementos: str = Form(default=""),
    lugar: str = Form(default=""),
    latitud: float | None = Form(default=None),
    longitud: float | None = Form(default=None),
    precision: float | None = Form(default=None),
    authorization: str | None = Header(default=None),
) -> CargaResponse:
    """Sube un archivo desde el chat. Exige sesión con nivel reportador; el
    modelo revisa el contenido y solo se guarda si tiene relación con COLFLUX y
    coincide con la `descripcion` que dio la persona antes de elegirlo.
    Si es un archivo de datos y le faltan campos obligatorios, responde
    `pendiente` con lo que falta; el chat reenvía el archivo con las
    respuestas de la persona en `complementos` (una por línea).
    `lugar` es obligatorio; `latitud`, `longitud` y `precision` (metros) son
    la ubicación del dispositivo al subir, si el navegador la dio."""
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
            lugar,
            (latitud, longitud, precision) if latitud is not None and longitud is not None else None,
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
