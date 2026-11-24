"""Cliente sobre la API pública, ya desplegada, de colflux-backend-ia.
Solo consume endpoints GET existentes — no toca ese repositorio ni su base
de datos."""

import httpx

from mcp_server.config import BACKEND_API_BASE_URL

GASES = {"CO2", "CH4", "N2O"}


def _get(path: str, params: dict | None = None, token: str | None = None) -> dict:
    headers = {"Authorization": f"Token {token}"} if token else {}
    with httpx.Client(base_url=BACKEND_API_BASE_URL, timeout=30) as client:
        response = client.get(
            path, params={k: v for k, v in (params or {}).items() if v is not None}, headers=headers,
        )
        response.raise_for_status()
        return response.json()


def _post(path: str, json_body: dict, token: str | None = None) -> dict:
    headers = {"Authorization": f"Token {token}"} if token else {}
    with httpx.Client(base_url=BACKEND_API_BASE_URL, timeout=30) as client:
        response = client.post(path, json=json_body, headers=headers)
        response.raise_for_status()
        return response.json()


def get_sitios(filtro: str | None = None) -> list[dict]:
    data = _get("/api/geo/sitios/")
    sitios = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        nombre = props.get("nombre", "")
        if filtro and filtro.lower() not in nombre.lower():
            continue
        lon, lat = feature.get("geometry", {}).get("coordinates", [None, None])
        sitios.append({
            "id": props.get("id"),
            "nombre": nombre,
            "latitud": lat,
            "longitud": lon,
            "resumen_por_gas": props.get("resumen_por_gas", {}),
        })
    return sitios


def resolve_sitio(nombre: str) -> tuple[dict | None, dict | None]:
    """Busca un sitio por nombre parcial. Devuelve (sitio, error)."""
    if not nombre:
        return None, None
    matches = get_sitios(filtro=nombre)
    if not matches:
        return None, {
            "error": f"No hay ningún sitio cuyo nombre contenga '{nombre}'.",
            "sugerencia": "Usa listar_sitios para ver los nombres disponibles.",
        }
    if len(matches) > 1:
        return None, {
            "error": f"'{nombre}' coincide con varios sitios.",
            "candidatos": [s["nombre"] for s in matches],
        }
    return matches[0], None


def get_resumen(nivel: str, gas: str | None = None, desde: str | None = None,
                hasta: str | None = None, sitio: int | None = None) -> dict:
    return _get("/api/geo/resumen/", {"nivel": nivel, "gas": gas, "desde": desde, "hasta": hasta, "sitio": sitio})


def get_series(gas: str | None = None, desde: str | None = None,
                hasta: str | None = None, sitio: int | None = None) -> list[dict]:
    data = _get("/api/geo/series/", {"gas": gas, "desde": desde, "hasta": hasta, "sitio": sitio})
    return data.get("resultados", [])


def get_campos_destino() -> dict:
    # campos_destino ya no enumera instancias FK (ver historial de la tarea:
    # ese N+1 hacía tardar ~54s y agotaba el timeout de gunicorn). Ahora solo
    # devuelve la estructura estática del catálogo, suficiente para proponer
    # a qué campo corresponde cada columna del archivo.
    return _get("/api/etl/campos-destino/")


def get_carga_ia(carga_id: int, token: str) -> dict:
    return _get(f"/api/ia-carga/{carga_id}/", token=token)


def confirmar_mapeo_ia(carga_id: int, mapeos: list[dict], token: str) -> dict:
    return _post(f"/api/ia-carga/{carga_id}/confirmar/", {"mapeos": mapeos}, token=token)
