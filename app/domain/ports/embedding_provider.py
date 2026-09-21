from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, texts: list[str], kind: str = "passage") -> list[list[float]]:
        raise NotImplementedError
