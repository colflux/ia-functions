import psycopg
from pgvector.psycopg import register_vector

from app.config import settings

EMBEDDING_DIM = 384  # all-MiniLM-L6-v2


def get_connection() -> psycopg.Connection:
    conn = psycopg.connect(settings.database_url, autocommit=True)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    register_vector(conn)
    return conn


def init_schema() -> None:
    with get_connection() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS document_chunks (
                id SERIAL PRIMARY KEY,
                source TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding VECTOR({EMBEDDING_DIM}) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS document_chunks_embedding_idx
            ON document_chunks USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 100)
            """
        )
