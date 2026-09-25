from typing import Any

from pydantic import BaseModel


class IngestRequest(BaseModel):
    source: str
    text: str
    collection: str = "documents"


class IngestResponse(BaseModel):
    chunks_indexed: int


class CargaResponse(BaseModel):
    aceptado: bool
    mensaje: str
    tipo: str | None = None
    fragmentos: int = 0
    archivo: str | None = None
    pendiente: bool = False
    faltantes: list[str] = []
    reintentar: bool = False
    lugar: str | None = None


class DescripcionRequest(BaseModel):
    descripcion: str


class LugarRequest(BaseModel):
    lugar: str


class ChatRequest(BaseModel):
    message: str
    usuario: str = "anonimo"
    # Token de sesión del backend. Solo lo usan las herramientas que escriben
    # (registrar mediciones), que verifican el nivel con el backend.
    token: str | None = None


class Source(BaseModel):
    source: str
    content: str
    score: float


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
    herramientas: list[str] = []
    pendiente_de_confirmacion: dict[str, Any] | None = None
    # Excel preparado en esta respuesta: [{"archivo", "nombre", "filas", "hojas"}]
    descargas: list[dict[str, Any]] = []


class HistorialMessage(BaseModel):
    role: str
    content: str


class HistorialResponse(BaseModel):
    messages: list[HistorialMessage]
