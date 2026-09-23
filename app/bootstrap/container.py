"""Composition root: el único lugar que conoce todas las implementaciones
concretas y decide, según config.py, cuál usar detrás de cada puerto."""

import sys
from functools import lru_cache

from app.adapters.embeddings.sentence_transformers_provider import SentenceTransformersProvider
from app.adapters.llm.anthropic import AnthropicProvider
from app.adapters.llm.cerebras import CerebrasProvider
from app.adapters.llm.gemini import GeminiProvider
from app.adapters.llm.groq import GroqProvider
from app.adapters.llm.ollama import OllamaProvider
from app.adapters.persistence.postgres_conversation_repo import PostgresConversationRepository
from app.adapters.tools.mcp_tool_provider import McpToolProvider
from app.adapters.tools.rag_tool_provider import RagToolProvider
from app.adapters.vectorstore.pgvector_store import PgVectorStore
from app.config import parsed_mcp_servers, settings
from app.domain.ports.llm_provider import LLMProvider
from app.domain.services.agent_orchestrator import AgentOrchestrator
from app.domain.services.rag_service import RagService
from app.domain.services.tool_registry import ToolRegistry
from app.domain.services.tool_router import ToolRouter
from app.adapters.auth.backend_user_directory import BackendUserDirectory
from app.adapters.storage.lightsail_bucket import LightsailBucket
from app.domain.services.carga_documentos import CargaDocumentos


@lru_cache
def get_llm_provider() -> LLMProvider:
    provider = settings.llm_provider.lower()
    if provider == "groq":
        return GroqProvider()
    if provider == "cerebras":
        return CerebrasProvider()
    if provider == "gemini":
        return GeminiProvider()
    if provider == "ollama":
        return OllamaProvider()
    if provider == "anthropic":
        return AnthropicProvider()
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider}")


@lru_cache
def get_embedding_provider() -> SentenceTransformersProvider:
    """Una sola instancia: el modelo ocupa 1,2 GB y lo comparten el RAG y el
    enrutador de herramientas."""
    return SentenceTransformersProvider(settings.embedding_model)


@lru_cache
def get_rag_service() -> RagService:
    embeddings = get_embedding_provider()
    vector_store = PgVectorStore()
    return RagService(embeddings, vector_store)


@lru_cache
def get_tool_registry() -> ToolRegistry:
    providers = [
        RagToolProvider(get_rag_service(), settings.retrieval_top_k, settings.retrieval_min_score),
    ]
    for name, url in parsed_mcp_servers().items():
        try:
            providers.append(McpToolProvider(url))
        except Exception as exc:
            # Degradación controlada: si un servidor MCP no responde (o no
            # está desplegado todavía), el agente sigue funcionando solo
            # con RAG en vez de caerse.
            print(f"[mcp] no se pudo conectar a '{name}' ({url}): {exc}", file=sys.stderr)
    return ToolRegistry(providers)


@lru_cache
def get_conversation_repository() -> PostgresConversationRepository:
    return PostgresConversationRepository()


@lru_cache
def get_orchestrator() -> AgentOrchestrator:
    return AgentOrchestrator(get_llm_provider(), get_tool_registry(),
                             get_conversation_repository(),
                             ToolRouter(get_embedding_provider()))


@lru_cache
def get_carga_documentos() -> CargaDocumentos:
    almacen = None
    if settings.bucket_name:
        almacen = LightsailBucket(
            settings.bucket_name,
            settings.bucket_region,
            settings.bucket_access_key_id,
            settings.bucket_secret_access_key,
        )
    return CargaDocumentos(
        usuarios=BackendUserDirectory(settings.backend_api_base_url),
        almacen=almacen,
        llm=get_llm_provider(),
        rag=get_rag_service(),
        max_bytes=settings.max_upload_mb * 1024 * 1024,
    )
