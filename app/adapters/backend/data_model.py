from typing import Any

import httpx

from app.domain.ports.data_model import DataModelCatalog


class BackendDataModel(DataModelCatalog):
    """Lee el modelo de datos del ETL del backend, que es la fuente de verdad:
    si el modelo cambia, la validación de archivos cambia con él."""

    def __init__(self, base_url: str, timeout: float = 15.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def campos(self, autorizacion: str) -> dict[str, list[dict[str, Any]]]:
        respuesta = httpx.get(
            f"{self._base_url}/api/etl/campos-destino/",
            headers={"Authorization": autorizacion},
            timeout=self._timeout,
        )
        respuesta.raise_for_status()
        return respuesta.json()["modelos"]

    def sitios(self) -> list[dict[str, Any]]:
        respuesta = httpx.get(f"{self._base_url}/api/geo/sitios/", timeout=self._timeout)
        respuesta.raise_for_status()
        return [
            {
                "id": f["properties"]["id"],
                "nombre": f["properties"].get("nombre") or "",
                "unidades": [u["nombre"] for u in f["properties"].get("unidades_muestreo") or []],
            }
            for f in respuesta.json()["features"]
        ]
