from abc import ABC, abstractmethod

from app.domain.models import RetrievedChunk


class VectorStore(ABC):
    @abstractmethod
    def upsert(
        self,
        source: str,
        chunks: list[str],
        embeddings: list[list[float]],
        collection: str,
    ) -> int:
        raise NotImplementedError

    @abstractmethod
    def search(
        self,
        query_embedding: list[float],
        top_k: int,
        collection: str,
    ) -> list[RetrievedChunk]:
        raise NotImplementedError

    @abstractmethod
    def buscar_texto(self, terminos: list[str], prefijo_fuente: str, limite: int) -> list[RetrievedChunk]:
        """Fragmentos de fuentes que empiezan por `prefijo_fuente` y contienen
        TODOS los términos, tal cual (sin distinguir mayúsculas). Sirve donde la
        búsqueda por significado falla: fechas, coordenadas, códigos."""
        raise NotImplementedError

    @abstractmethod
    def primeros_fragmentos(
        self, prefijo_fuente: str, limite: int, fuentes: list[str] | None = None
    ) -> list[RetrievedChunk]:
        """Primer fragmento de cada fuente (el que trae nombre, descripción y
        encabezados), de la más reciente a la más antigua."""
        raise NotImplementedError

    @abstractmethod
    def pendientes_de_reindexar(self, limite: int) -> list[tuple[int, str]]:
        """Fragmentos cuyo vector está vacío — por ejemplo tras cambiar de
        modelo de embeddings. Devuelve pares (id, texto)."""
        raise NotImplementedError

    @abstractmethod
    def guardar_embeddings(self, pares: list[tuple[int, list[float]]]) -> None:
        """Escribe en lote los vectores recalculados de fragmentos existentes."""
        raise NotImplementedError
