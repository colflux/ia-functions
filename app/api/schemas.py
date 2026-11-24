from typing import Any

from pydantic import BaseModel


class IngestRequest(BaseModel):
    source: str
    text: str
    collection: str = "documents"


class IngestResponse(BaseModel):
    chunks_indexed: int


class ChatRequest(BaseModel):
    message: str
    usuario: str = "anonimo"
    token: str | None = None
    """Token `Authorization: Token <token>` del usuario autenticado en el
    backend Django (el mismo que usa en /reportar). Solo se reenvía a las
    tools que escriben en el backend en su nombre (ver AUTH_REQUIRED_TOOLS
    en agent_orchestrator.py); nunca se expone al LLM."""


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
