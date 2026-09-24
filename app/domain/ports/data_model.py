from abc import ABC, abstractmethod
from typing import Any


class DataModelCatalog(ABC):
    """El modelo de datos de COLFLUX: qué campos tiene cada modelo."""

    @abstractmethod
    def campos(self, autorizacion: str) -> dict[str, list[dict[str, Any]]]:
        """{modelo: [{"nombre", "verbose_name", "requerido", "choices", ...}]}"""
        raise NotImplementedError

    @abstractmethod
    def sitios(self) -> list[dict[str, Any]]:
        """Sitios registrados: [{"id", "nombre", "unidades": [nombres de unidades de muestreo]}]"""
        raise NotImplementedError
