from app.db.session import get_connection


def insert_chunks(source: str, chunks: list[str], embeddings: list[list[float]]) -> int:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO document_chunks (source, content, embedding) VALUES (%s, %s, %s)",
                list(zip([source] * len(chunks), chunks, embeddings)),
            )
    return len(chunks)


def search(query_embedding: list[float], top_k: int) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source, content, 1 - (embedding <=> %s) AS score
                FROM document_chunks
                ORDER BY embedding <=> %s
                LIMIT %s
                """,
                (query_embedding, query_embedding, top_k),
            )
            rows = cur.fetchall()
    return [{"source": r[0], "content": r[1], "score": r[2]} for r in rows]
