from abc import ABC, abstractmethod


class FileStorage(ABC):
    """Dónde se guardan los archivos originales que se suben."""

    @abstractmethod
    def subir(self, clave: str, contenido: bytes, tipo_contenido: str, metadatos: dict[str, str]) -> None:
        raise NotImplementedError

    @abstractmethod
    def existe(self, clave: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def leer(self, clave: str) -> bytes | None:
        """Contenido guardado en `clave`, o None si no existe."""
        raise NotImplementedError

    @abstractmethod
    def enlace_descarga(self, clave: str, nombre: str, segundos: int) -> str | None:
        """Enlace temporal para descargar `clave` con el nombre `nombre`, o None si no existe."""
        raise NotImplementedError

    @abstractmethod
    def borrar(self, clave: str) -> None:
        raise NotImplementedError
