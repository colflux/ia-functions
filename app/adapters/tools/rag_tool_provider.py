"""Expone el RAG (documentos y diccionario de campo) como tools locales,
para que el orquestador las trate igual que las tools remotas de MCP.

Convención: si el resultado de una tool trae la clave "sources", el
orquestador la usa para armar la lista de fuentes citadas en la respuesta y
la retira antes de devolverle el resultado al modelo."""

from typing import Any

from app.domain.models import ToolSpec
from app.domain.ports.tool_provider import ToolProvider
from app.domain.services.carga_documentos import es_archivo_subido, nombre_visible
from app.domain.services.rag_service import RagService


MAX_PALABRAS_LISTADO = 3


class RagToolProvider(ToolProvider):
    def __init__(self, rag_service: RagService, top_k: int, min_score: float, depurador=None) -> None:
        self._rag = rag_service
        self._top_k = top_k
        self._min_score = min_score
        self._depurador = depurador  # retira archivos borrados del bucket antes de buscar

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
            if self._depurador:
                self._depurador.depurar()
            resultado = self._search(texto, collection="documents")
            if len(texto.split()) <= MAX_PALABRAS_LISTADO:
                # Buscar "entrevistas" suele ser preguntar qué hay, y la búsqueda por
                # significado trae solo lo más parecido: se pide también la lista
                # completa de archivos subidos (el orquestador la ejecuta).
                resultado["consultar_tambien"] = {
                    "herramienta": "consultar_archivos_subidos",
                    "argumentos": {"solo_lista": True},
                }
            return resultado
        if name == "buscar_diccionario":
            return self._search(texto, collection="dictionary")
        return {"error": f"Herramienta desconocida: {name}"}

    def _search(self, texto: str, collection: str) -> dict[str, Any]:
        matches = self._rag.retrieve(texto, self._top_k, self._min_score, collection=collection)
        if not matches:
            return {"resultados": [], "mensaje": "Nada relevante encontrado."}
        sources = [{"source": m.source, "content": m.content, "score": m.score} for m in matches]
        # Al modelo, el nombre legible del archivo; al chat, la clave del bucket,
        # que es la que permite ofrecer la descarga del original.
        # Solo las claves del bucket se acortan; un término como
        # «Olor a huevo podrido / podrido» quedaba reducido a « podrido».
        resultados = [{**s, "source": nombre_visible(s["source"]) if es_archivo_subido(s["source"]) else s["source"]}
                      for s in sources]
        return {"resultados": resultados, "sources": sources}
