"""Consultas de mediciones de gases (CO2/CH4/N2O), vía la API pública del
backend (/api/geo/resumen/ y /api/geo/series/). Variables ambientales
(temperatura, humedad, etc.) no están expuestas por el backend todavía —
ver arquitectura.md, sección "fuera de alcance"."""

from mcp_server import backend_client
from mcp_server.backend_client import GASES


def consultar_promedio(variable: str, sitio: str | None = None,
                        desde: str | None = None, hasta: str | None = None) -> dict:
    """Promedio, mínimo y máximo de un gas (CO2, CH4 o N2O), opcionalmente
    acotado por sitio y rango de fechas (AAAA-MM-DD)."""
    variable = (variable or "").strip().upper()
    if variable not in GASES:
        return {"error": f"Variable no reconocida: '{variable}'.", "variables_validas": sorted(GASES)}

    sitio_obj, error = (None, None)
    if sitio:
        sitio_obj, error = backend_client.resolve_sitio(sitio)
        if error:
            return error

    if sitio_obj:
        resumen = backend_client.get_resumen("sitio", gas=variable, desde=desde, hasta=hasta, sitio=sitio_obj["id"])
        features = resumen.get("features", [])
        if not features:
            return {"variable": variable, "sitio": sitio_obj["nombre"], "sin_datos": True}
        props = backend_client.buscar_feature(resumen, sitio_obj["id"])
        if props is None:
            return {"variable": variable, "sitio": sitio_obj["nombre"], "sin_datos": True}
        return {
            "variable": variable, "sitio": sitio_obj["nombre"],
            "n": props.get("total_muestras"), "promedio": props.get("promedio"),
            "minimo": props.get("minimo"), "maximo": props.get("maximo"),
        }

    series = backend_client.get_series(gas=variable, desde=desde, hasta=hasta)
    valores = [s["valor"] for s in series if s.get("valor") is not None]
    if not valores:
        return {"variable": variable, "sitio": "todos los sitios", "sin_datos": True}
    return {
        "variable": variable, "sitio": "todos los sitios", "n": len(valores),
        "promedio": round(sum(valores) / len(valores), 4), "minimo": min(valores), "maximo": max(valores),
    }


def consultar_ultima_medicion(variable: str, sitio: str | None = None) -> dict:
    """La medición más reciente de un gas (CO2, CH4 o N2O), opcionalmente en un sitio."""
    variable = (variable or "").strip().upper()
    if variable not in GASES:
        return {"error": f"Variable no reconocida: '{variable}'.", "variables_validas": sorted(GASES)}

    sitio_obj = None
    if sitio:
        sitio_obj, error = backend_client.resolve_sitio(sitio)
        if error:
            return error

    if sitio_obj:
        resumen = backend_client.get_resumen("sitio", gas=variable, sitio=sitio_obj["id"])
        features = resumen.get("features", [])
        props = backend_client.buscar_feature(resumen, sitio_obj["id"])
        if props is None or not props.get("ultima_medicion"):
            return {"variable": variable, "sitio": sitio_obj["nombre"], "sin_datos": True}
        return {"variable": variable, "sitio": sitio_obj["nombre"], "ultima": props["ultima_medicion"]}

    series = backend_client.get_series(gas=variable)
    if not series:
        return {"variable": variable, "sin_datos": True}
    ultima = max(series, key=lambda s: s["fecha"])
    return {"variable": variable, "ultima": ultima}
