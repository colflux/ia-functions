from abc import ABC, abstractmethod


class FileStorage(ABC):
    """Dónde se guardan los archivos originales que se suben."""

    @abstractmethod
    def subir(self, clave: str, contenido: bytes, tipo_contenido: str, metadatos: dict[str, str]) -> None:
        raise NotImplementedError

    @abstractmethod
    def borrar(self, clave: str) -> None:
        raise NotImplementedError
