"""Cliente sobre la API pública, ya desplegada, de colflux-backend-ia.
Solo consume endpoints GET existentes — no toca ese repositorio ni su base
de datos."""

import re

import httpx

from mcp_server.config import BACKEND_API_BASE_URL

GASES = {"CO2", "CH4", "N2O"}


def _get(path: str, params: dict | None = None) -> dict:
    with httpx.Client(base_url=BACKEND_API_BASE_URL, timeout=30) as client:
        response = client.get(path, params={k: v for k, v in (params or {}).items() if v is not None})
        response.raise_for_status()
        return response.json()


def get_sitios(filtro: str | None = None) -> list[dict]:
    data = _get("/api/geo/sitios/")
    sitios = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        nombre = (props.get("nombre") or "").strip()
        vereda = props.get("vereda") or ""
        municipio = props.get("municipio") or ""
        if not nombre:
            partes = ", ".join(p for p in (vereda, municipio) if p)
            nombre = "Sitio %s%s" % (props.get("id"), " (" + partes + ")" if partes else "")
        if filtro and filtro.lower() not in " ".join([nombre, vereda, municipio]).lower():
            continue
        lon, lat = feature.get("geometry", {}).get("coordinates", [None, None])
        sitios.append({
            "id": props.get("id"),
            "nombre": nombre,
            "vereda": vereda or None,
            "municipio": municipio or None,
            "latitud": lat,
            "longitud": lon,
        })
    return sitios


def resolve_sitio(nombre: str) -> tuple[dict | None, dict | None]:
    """Busca un sitio por nombre parcial. Devuelve (sitio, error)."""
    if not nombre:
        return None, None
    m = re.search(r"\b(\d+)\b", str(nombre))
    if m:
        por_id = [s for s in get_sitios() if s["id"] == int(m.group(1))]
        if por_id:
            return por_id[0], None
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


def buscar_feature(resumen: dict, sitio_id: int) -> dict | None:
    """Selecciona del resumen el feature del sitio pedido, en vez de asumir que
    viene primero: al consultar por otros niveles el resumen agrupa varios
    sitios, y el orden no está garantizado."""
    for f in resumen.get("features", []):
        if f.get("properties", {}).get("id") == sitio_id:
            return f["properties"]
    return None
