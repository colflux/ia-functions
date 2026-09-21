from pgvector.utils import Vector

from app.db.session import get_connection
from app.domain.models import RetrievedChunk
from app.domain.ports.vector_store import VectorStore


class PgVectorStore(VectorStore):
    def upsert(
        self,
        source: str,
        chunks: list[str],
        embeddings: list[list[float]],
        collection: str,
    ) -> int:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO document_chunks (source, content, collection, embedding) "
                    "VALUES (%s, %s, %s, %s)",
                    [
                        (source, chunk, collection, Vector(embedding))
                        for chunk, embedding in zip(chunks, embeddings)
                    ],
                )
        return len(chunks)

    def search(
        self,
        query_embedding: list[float],
        top_k: int,
        collection: str,
    ) -> list[RetrievedChunk]:
        vector = Vector(query_embedding)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT source, content, 1 - (embedding <=> %s) AS score
                    FROM document_chunks
                    WHERE collection = %s
                    ORDER BY embedding <=> %s
                    LIMIT %s
                    """,
                    (vector, collection, vector, top_k),
                )
                rows = cur.fetchall()
        return [RetrievedChunk(source=r[0], content=r[1], score=r[2]) for r in rows]

    def pendientes_de_reindexar(self, limite: int) -> list[tuple[int, str]]:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, content FROM document_chunks "
                    "WHERE embedding IS NULL ORDER BY id LIMIT %s",
                    (limite,),
                )
                return [(r[0], r[1]) for r in cur.fetchall()]

    def guardar_embedding(self, chunk_id: int, embedding: list[float]) -> None:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE document_chunks SET embedding = %s WHERE id = %s",
                    (Vector(embedding), chunk_id),
                )
