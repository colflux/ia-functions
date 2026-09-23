from pgvector.utils import Vector

from app.db.session import get_connection
from app.domain.models import RetrievedChunk
from app.domain.ports.vector_store import VectorStore


CON_TILDE = "áéíóúüñàèìòù"
SIN_TILDE = "aeiouunaeiou"


def _literal(texto: str) -> str:
    """Escapa los comodines de LIKE para buscar el texto tal cual."""
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _sin_tildes(texto: str) -> str:
    return texto.lower().translate(str.maketrans(CON_TILDE, SIN_TILDE))


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

    def buscar_texto(self, terminos: list[str], prefijo_fuente: str, limite: int) -> list[RetrievedChunk]:
        # Sin distinguir mayúsculas ni tildes: "doña" encuentra "dona" y al revés.
        # Sin términos, trae todos los fragmentos de las fuentes con ese prefijo
        # (así se lee un archivo completo pasando su clave como prefijo).
        patrones = [f"%{_literal(_sin_tildes(t))}%" for t in terminos]
        condicion = "AND translate(lower(content), %s, %s) LIKE ALL(%s::text[])" if patrones else ""
        parametros = (CON_TILDE, SIN_TILDE, patrones) if patrones else ()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT source, content
                    FROM document_chunks
                    WHERE source LIKE %s {condicion}
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (f"{_literal(prefijo_fuente)}%", *parametros, limite),
                )
                rows = cur.fetchall()
        return [RetrievedChunk(source=r[0], content=r[1], score=1.0) for r in rows]

    def primeros_fragmentos(
        self, prefijo_fuente: str, limite: int, fuentes: list[str] | None = None
    ) -> list[RetrievedChunk]:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT source, content
                    FROM document_chunks
                    WHERE id IN (
                        SELECT min(id) FROM document_chunks
                        WHERE source LIKE %s AND (%s::text[] IS NULL OR source = ANY(%s::text[]))
                        GROUP BY source
                    )
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (f"{_literal(prefijo_fuente)}%", fuentes, fuentes, limite),
                )
                rows = cur.fetchall()
        return [RetrievedChunk(source=r[0], content=r[1], score=1.0) for r in rows]

    def pendientes_de_reindexar(self, limite: int) -> list[tuple[int, str]]:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, content FROM document_chunks "
                    "WHERE embedding IS NULL ORDER BY id LIMIT %s",
                    (limite,),
                )
                return [(r[0], r[1]) for r in cur.fetchall()]

    def guardar_embeddings(self, pares: list[tuple[int, list[float]]]) -> None:
        if not pares:
            return
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "UPDATE document_chunks SET embedding = %s WHERE id = %s",
                    [(Vector(vector), chunk_id) for chunk_id, vector in pares],
                )
