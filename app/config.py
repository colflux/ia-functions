from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    llm_provider: str = "groq"  # groq | gemini | ollama | anthropic

    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-1.5-flash"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "llama3.1"

    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    database_url: str = "postgresql://colflux:colflux@db:5432/colflux_ia"

    retrieval_top_k: int = 4
    retrieval_min_score: float = 0.3

    class Config:
        env_file = ".env"


settings = Settings()
