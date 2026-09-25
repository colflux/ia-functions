from app.domain.models import RetrievedChunk
from app.domain.ports.embedding_provider import EmbeddingProvider
from app.domain.ports.vector_store import VectorStore
from app.domain.services.chunking import chunk_text

DEFAULT_COLLECTION = "documents"


class RagService:
    def __init__(self, embeddings: EmbeddingProvider, vector_store: VectorStore) -> None:
        self._embeddings = embeddings
        self._vector_store = vector_store

    def ingest_document(self, source: str, text: str, collection: str = DEFAULT_COLLECTION) -> int:
        chunks = chunk_text(text)
        if not chunks:
            return 0
        embeddings = self._embeddings.embed(chunks)
        return self._vector_store.upsert(source, chunks, embeddings, collection)

    def actualizar_fuente(self, source: str, text: str, collection: str = DEFAULT_COLLECTION) -> int | None:
        """Reemplaza los fragmentos de `source` por los de `text` en una sola
        transacción. Si el texto no cambió no recalcula nada y devuelve None."""
        chunks = chunk_text(text)
        if chunks == self._vector_store.fragmentos(source):
            return None
        embeddings = self._embeddings.embed(chunks) if chunks else []
        return self._vector_store.reemplazar(source, chunks, embeddings, collection)

    def retrieve(
        self,
        question: str,
        top_k: int,
        min_score: float,
        collection: str = DEFAULT_COLLECTION,
    ) -> list[RetrievedChunk]:
        query_embedding = self._embeddings.embed([question], kind="query")[0]
        matches = self._vector_store.search(query_embedding, top_k, collection)
        return [m for m in matches if m.score >= min_score]

    def tiene_fuente(self, fuente: str) -> bool:
        return fuente in self._vector_store.fuentes(fuente)

    def reindexar_pendientes(self, lote: int = 32) -> int:
        """Recalcula los vectores de los fragmentos que no tienen ninguno —
        por ejemplo después de cambiar de modelo de embeddings. El texto ya
        está guardado, así que no hace falta volver a subir los documentos."""
        total = 0
        while True:
            pendientes = self._vector_store.pendientes_de_reindexar(lote)
            if not pendientes:
                return total
            vectores = self._embeddings.embed([texto for _, texto in pendientes])
            pares = [(cid, v) for (cid, _), v in zip(pendientes, vectores)]
            self._vector_store.guardar_embeddings(pares)
            total += len(pendientes)
