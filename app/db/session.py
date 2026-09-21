import os
import re

import psycopg
from pgvector.psycopg import register_vector

from app.config import settings

EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "384"))


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
                collection TEXT NOT NULL DEFAULT 'documents',
                embedding VECTOR({EMBEDDING_DIM}) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        _migrar_dimension(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS document_chunks_collection_idx "
            "ON document_chunks (collection)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                external_user_id TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                external_user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tools_used TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS messages_user_idx "
            "ON messages (external_user_id, created_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_confirmations (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                external_user_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                arguments JSONB NOT NULL,
                resolved BOOLEAN NOT NULL DEFAULT false,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS pending_confirmations_user_idx "
            "ON pending_confirmations (external_user_id, resolved, created_at)"
        )


def _dimension_actual(conn) -> int | None:
    """Dimensión declarada hoy en document_chunks.embedding, o None si la
    tabla todavía no existe."""
    fila = conn.execute(
        "SELECT format_type(a.atttypid, a.atttypmod) "
        "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
        "WHERE c.relname = %s AND a.attname = %s AND NOT a.attisdropped",
        ("document_chunks", "embedding"),
    ).fetchone()
    if not fila:
        return None
    m = re.search(r"\((\d+)\)", fila[0])
    return int(m.group(1)) if m else None


def _migrar_dimension(conn) -> None:
    """Al cambiar de modelo de embeddings la dimensión deja de coincidir y
    los vectores guardados dejan de ser comparables entre sí. Se conserva el
    texto y se vacían los vectores: se recalculan al arrancar."""
    actual = _dimension_actual(conn)
    if actual is None or actual == EMBEDDING_DIM:
        return
    print(f"[schema] embedding: vector({actual}) -> vector({EMBEDDING_DIM}); se reindexará el contenido", flush=True)
    conn.execute("ALTER TABLE document_chunks DROP COLUMN embedding")
    conn.execute(f"ALTER TABLE document_chunks ADD COLUMN embedding VECTOR({EMBEDDING_DIM})")
