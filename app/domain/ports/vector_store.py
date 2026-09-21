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
    def pendientes_de_reindexar(self, limite: int) -> list[tuple[int, str]]:
        """Fragmentos cuyo vector está vacío — por ejemplo tras cambiar de
        modelo de embeddings. Devuelve pares (id, texto)."""
        raise NotImplementedError

    @abstractmethod
    def guardar_embedding(self, chunk_id: int, embedding: list[float]) -> None:
        """Escribe el vector recalculado de un fragmento existente."""
        raise NotImplementedError
