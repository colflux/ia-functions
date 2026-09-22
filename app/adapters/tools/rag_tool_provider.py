"""Expone el RAG (documentos y diccionario de campo) como tools locales,
para que el orquestador las trate igual que las tools remotas de MCP.

Convención: si el resultado de una tool trae la clave "sources", el
orquestador la usa para armar la lista de fuentes citadas en la respuesta y
la retira antes de devolverle el resultado al modelo."""

from typing import Any

from app.domain.models import ToolSpec
from app.domain.ports.tool_provider import ToolProvider
from app.domain.services.rag_service import RagService


class RagToolProvider(ToolProvider):
    def __init__(self, rag_service: RagService, top_k: int, min_score: float) -> None:
        self._rag = rag_service
        self._top_k = top_k
        self._min_score = min_score

    def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="buscar_documentos",
                description=(
                    "Busca por significado en entrevistas, informes y notas de campo "
                    "indexadas. Devuelve los fragmentos más relevantes con su fuente."
                ),
                parameters={
                    "type": "object",
                    "properties": {"texto": {"type": "string", "description": "Lo que se quiere encontrar."}},
                    "required": ["texto"],
                },
            ),
            ToolSpec(
                name="buscar_diccionario",
                description=(
                    "Busca por significado en el diccionario de observaciones cualitativas "
                    "de campo: olores, colores, texturas del suelo, y frases como que el "
                    "suelo esta respirando fuerte, que el humedal esta hirviendo o que la "
                    "zona esta apagada. Usala siempre que la pregunta incluya una expresion "
                    "de ese tipo, antes de consultar las mediciones."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "texto": {"type": "string", "description": "La observación tal como la describió la persona."}
                    },
                    "required": ["texto"],
                },
            ),
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        texto = (arguments.get("texto") or "").strip()
        if name == "buscar_documentos":
            return self._search(texto, collection="documents")
        if name == "buscar_diccionario":
            return self._search(texto, collection="dictionary")
        return {"error": f"Herramienta desconocida: {name}"}

    def _search(self, texto: str, collection: str) -> dict[str, Any]:
        matches = self._rag.retrieve(texto, self._top_k, self._min_score, collection=collection)
        if not matches:
            return {"resultados": [], "mensaje": "Nada relevante encontrado."}
        sources = [{"source": m.source, "content": m.content, "score": m.score} for m in matches]
        return {"resultados": sources, "sources": sources}
