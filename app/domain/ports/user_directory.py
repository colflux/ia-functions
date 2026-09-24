from abc import ABC, abstractmethod
from typing import Any


class UserDirectory(ABC):
    """Quién es el dueño de una sesión y qué nivel de acceso tiene."""

    @abstractmethod
    def usuario_por_token(self, autorizacion: str) -> dict[str, Any] | None:
        """Datos del usuario dueño del encabezado `Authorization`, o None si
        la sesión no es válida. Una cuenta sin usuario de dominio devuelve un
        diccionario sin nivel."""
        raise NotImplementedError
