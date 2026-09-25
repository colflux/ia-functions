"""Composition root: el único lugar que conoce todas las implementaciones
concretas y decide, según config.py, cuál usar detrás de cada puerto."""

import sys
from functools import lru_cache

from app.adapters.auth.backend_user_directory import BackendUserDirectory
from app.adapters.backend.data_model import BackendDataModel
from app.adapters.embeddings.sentence_transformers_provider import SentenceTransformersProvider
from app.adapters.llm.anthropic import AnthropicProvider
from app.adapters.llm.cerebras import CerebrasProvider
from app.adapters.llm.failover import FailoverProvider
from app.adapters.llm.gemini import GeminiProvider
from app.adapters.llm.groq import GroqProvider
from app.adapters.llm.ollama import OllamaProvider
from app.adapters.persistence.postgres_conversation_repo import PostgresConversationRepository
from app.adapters.storage.lightsail_bucket import LightsailBucket
from app.adapters.tools.archivos_subidos_tool_provider import ArchivosSubidosToolProvider
from app.adapters.tools.mediciones_chat_tool_provider import MedicionesChatToolProvider
from app.adapters.tools.mcp_tool_provider import McpToolProvider
from app.adapters.tools.rag_tool_provider import RagToolProvider
from app.adapters.vectorstore.pgvector_store import PgVectorStore
from app.adapters.vision.gemini_vision import GeminiVision
from app.config import parsed_mcp_servers, settings
from app.domain.ports.llm_provider import LLMProvider
from app.domain.services.agent_orchestrator import AgentOrchestrator
from app.domain.services.carga_documentos import CargaDocumentos
from app.domain.services.depuracion import DepuradorArchivos
from app.domain.services.descargas import Descargas
from app.domain.services.reglas_diccionario import ReglasDiccionario
from app.domain.services.rag_service import RagService
from app.domain.services.tool_registry import ToolRegistry
from app.domain.services.tool_router import ToolRouter
from app.domain.services.validacion_datos import ValidadorDatos


PROVEEDORES_LLM = {
    "cerebras": CerebrasProvider,
    "groq": GroqProvider,
    "gemini": GeminiProvider,
    "ollama": OllamaProvider,
    "anthropic": AnthropicProvider,
}


@lru_cache
def get_llm_provider() -> LLMProvider:
    """El proveedor principal (LLM_PROVIDER) y, detrás, los de respaldo
    (LLM_RESPALDO): si uno no puede atender una llamada, se pasa al siguiente."""
    nombres = [settings.llm_provider, *settings.llm_respaldo.split(",")]
    nombres = list(dict.fromkeys(n.strip().lower() for n in nombres if n.strip()))
    desconocidos = [n for n in nombres if n not in PROVEEDORES_LLM]
    if desconocidos:
        raise ValueError(f"Proveedor de modelo desconocido: {', '.join(desconocidos)}")
    return FailoverProvider([PROVEEDORES_LLM[n]() for n in nombres])


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
        RagToolProvider(get_rag_service(), settings.retrieval_top_k, settings.retrieval_min_score,
                        get_depurador()),
        ArchivosSubidosToolProvider(PgVectorStore(), get_depurador()),
        MedicionesChatToolProvider(BackendUserDirectory(settings.backend_api_base_url),
                                   BackendDataModel(settings.backend_api_base_url)),
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
                             ToolRouter(get_embedding_provider()),
                             ReglasDiccionario(PgVectorStore()),
                             get_descargas())


@lru_cache
def get_almacen() -> LightsailBucket | None:
    if not settings.bucket_name:
        return None
    return LightsailBucket(
        settings.bucket_name,
        settings.bucket_region,
        settings.bucket_access_key_id,
        settings.bucket_secret_access_key,
    )


@lru_cache
def get_depurador() -> DepuradorArchivos:
    return DepuradorArchivos(PgVectorStore(), get_almacen())


@lru_cache
def get_descargas() -> Descargas:
    return Descargas(get_almacen())


@lru_cache
def get_carga_documentos() -> CargaDocumentos:
    almacen = get_almacen()
    return CargaDocumentos(
        usuarios=BackendUserDirectory(settings.backend_api_base_url),
        almacen=almacen,
        llm=get_llm_provider(),
        rag=get_rag_service(),
        validador=ValidadorDatos(get_llm_provider(), BackendDataModel(settings.backend_api_base_url)),
        modelo=BackendDataModel(settings.backend_api_base_url),
        herramientas=get_tool_registry(),
        revisor_imagenes=(
            GeminiVision(settings.gemini_api_key, settings.image_model)
            if settings.gemini_api_key else None
        ),
        max_bytes=settings.max_upload_mb * 1024 * 1024,
    )
