from sentence_transformers import SentenceTransformer

from app.domain.ports.embedding_provider import EmbeddingProvider


class SentenceTransformersProvider(EmbeddingProvider):
    def __init__(self, model_name: str) -> None:
        self._model = SentenceTransformer(model_name)
        # Los modelos E5 exigen prefijos; otros modelos no los usan.
        self._usa_prefijos = "e5" in model_name.lower()

    def embed(self, texts: list[str], kind: str = "passage") -> list[list[float]]:
        if self._usa_prefijos:
            texts = [f"{kind}: {t}" for t in texts]
        return self._model.encode(texts, normalize_embeddings=True).tolist()
