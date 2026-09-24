from abc import ABC, abstractmethod
from typing import Any


class ImageReviewer(ABC):
    """Revisa una imagen con un modelo que puede verla."""

    @abstractmethod
    def revisar(self, contenido: bytes, mime: str, descripcion: str) -> dict[str, Any] | None:
        """{"relacionado": bool, "coincide": bool, "motivo": str, "descripcion": str,
        "observaciones": [rasgos visibles en lenguaje de campo]},
        o None si la respuesta del modelo no se pudo interpretar."""
        raise NotImplementedError
