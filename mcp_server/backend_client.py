"""Cliente sobre la API pública, ya desplegada, de colflux-backend-ia.
Solo consume endpoints GET existentes — no toca ese repositorio ni su base
de datos."""

import json
import math
import re

import httpx

from mcp_server.config import BACKEND_API_BASE_URL
from mcp_server.texto import normalizar

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
        if filtro and normalizar(filtro) not in normalizar(" ".join([nombre, vereda, municipio])):
            continue
        lon, lat = feature.get("geometry", {}).get("coordinates", [None, None])
        sitios.append({
            "id": props.get("id"),
            "nombre": nombre,
            "vereda": vereda or None,
            "municipio": municipio or None,
            "departamento": props.get("departamento") or None,
            "latitud": lat,
            "longitud": lon,
        })
    return sitios


def resolve_sitio(nombre: str) -> tuple[dict | None, dict | None]:
    """Busca un sitio por nombre parcial. Devuelve (sitio, error)."""
    if not nombre:
        return None, None
    m = re.search(r"(?<![0-9.])([0-9]+)(?![0-9.])", str(nombre))
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
            "candidatos": [{"id": s["id"], "nombre": s["nombre"]} for s in matches],
        }
    return matches[0], None


def get_resumen(nivel: str, gas: str | None = None, desde: str | None = None,
                hasta: str | None = None, sitio: int | None = None,
                categoria: str | None = None) -> dict:
    return _get("/api/geo/resumen/", {"nivel": nivel, "gas": gas, "desde": desde, "hasta": hasta, "sitio": sitio, "categoria": categoria})


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


VISTAS_DATOS = ("submuestra_gei", "unidad_muestreo", "clima", "mom", "cos", "biomasa")


def get_proyectos() -> list[dict]:
    """Proyectos con su id y su nombre, desde /api/proyectos/."""
    data = _get("/api/proyectos/")
    filas = data if isinstance(data, list) else data.get("results", [])
    return [{"id": p.get("id"), "nombre": (p.get("nombre") or "").strip()} for p in filas]


def resolve_proyecto(nombre: str) -> tuple[dict | None, dict | None]:
    """Busca un proyecto por nombre parcial o por id. Devuelve (proyecto, error)."""
    proyectos = get_proyectos()
    texto = str(nombre or "").strip()
    if texto.isdigit():
        por_id = [p for p in proyectos if p["id"] == int(texto)]
        if por_id:
            return por_id[0], None
    coincidencias = [p for p in proyectos if texto.lower() in p["nombre"].lower()]
    if not coincidencias:
        return None, {"error": "No encontré ese proyecto.", "proyectos": proyectos}
    return coincidencias[0], None


def get_datos(proyecto_id: int, vista: str, limite: int = 5, offset: int = 0,
              sitio: int | None = None, filtros: dict | None = None) -> dict:
    """Filas de una vista de datos de un proyecto, vía /api/proyectos/<id>/datos/.
    filtros es un diccionario con claves Modelo.campo que el backend aplica como
    coincidencia parcial; sirve también para fechas (2021-10-05 o 2021-10)."""
    params = {"vista": vista, "limite": limite, "offset": offset, "sitio": sitio}
    if filtros:
        params["filtros"] = json.dumps(filtros, ensure_ascii=False)
    return _get(f"/api/proyectos/{proyecto_id}/datos/", params)


def sitios_cerca(latitud: float, longitud: float, limite: int = 5) -> list[dict]:
    """Los sitios más cercanos a un punto, ordenados por distancia.

    Distancia aproximada en kilómetros: un grado de latitud son 111 km, y uno
    de longitud son 111 km por el coseno de la latitud. A la escala de
    Colombia el error es despreciable y evita depender de otra librería."""
    cos_lat = math.cos(math.radians(latitud))
    salida = []
    for item in get_sitios():
        if item["latitud"] is None or item["longitud"] is None:
            continue
        dy = (item["latitud"] - latitud) * 111.0
        dx = (item["longitud"] - longitud) * 111.0 * cos_lat
        # Cuatro decimales (0,1 m): con dos, 16 m y 6 m quedaban iguales.
        salida.append(dict(item, distancia_km=round(math.hypot(dx, dy), 4)))
    salida.sort(key=lambda x: x["distancia_km"])
    return salida[:limite]


_GEO_IDS: list[dict] | None = None


def get_geo_ids() -> list[dict]:
    """Ids de vereda, municipio y departamento de cada sitio.

    El endpoint de sitios solo trae los nombres, pero los filtros del backend
    piden ids. Se consulta una vez y se guarda en memoria."""
    global _GEO_IDS
    if _GEO_IDS is None:
        data = _get("/api/geo/resumen/", {"nivel": "sitio"})
        _GEO_IDS = [f.get("properties", {}) for f in data.get("features", [])]
    return _GEO_IDS


def get_mediciones(gas: str | None = None, desde: str | None = None,
                   hasta: str | None = None, sitio: int | None = None,
                   vereda: int | None = None, municipio: int | None = None,
                   departamento: int | None = None) -> list[dict]:
    """Mediciones individuales, sin agregar. Cada una conserva su unidad."""
    data = _get("/api/geo/series/", {"gas": gas, "desde": desde, "hasta": hasta,
                                     "sitio": sitio, "vereda": vereda,
                                     "municipio": municipio, "departamento": departamento})
    return data.get("resultados", [])
