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


class ChatRequest(BaseModel):
    message: str
    usuario: str = "anonimo"


class Source(BaseModel):
    source: str
    content: str
    score: float


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
    herramientas: list[str] = []
    pendiente_de_confirmacion: dict[str, Any] | None = None


class HistorialMessage(BaseModel):
    role: str
    content: str


class HistorialResponse(BaseModel):
    messages: list[HistorialMessage]
