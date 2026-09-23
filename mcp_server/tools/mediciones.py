"""Consultas agregadas del geoportal: flujos de gases (CO2/CH4/N2O), biomasa y
carbono orgánico del suelo, vía /api/geo/resumen/ y /api/geo/series/.

La materia orgánica muerta y las variables ambientales no son categorías de
este endpoint: se consultan con consultar_datos_campo (tools/datos.py)."""

from mcp_server import backend_client
from mcp_server.backend_client import GASES

CATEGORIAS = ("flujos", "biomasa", "cos", "produccion")

NOTA_SIN_UNIDAD = (
    "El backend no declara unidad porque el grupo mezcla varias. "
    "No supongas ninguna ni la inventes."
)


NOTA_MEZCLA = ("Este grupo mezcla unidades distintas, así que no existe un promedio único: "
               "cada unidad tiene el suyo. Muéstralos por separado y no los combines.")


def _por_unidad(gas, desde, hasta, sitio_id=None) -> list[dict]:
    """Promedio, mínimo y máximo dentro de cada unidad, nunca entre unidades."""
    grupos: dict = {}
    for f in backend_client.get_mediciones(gas=gas, desde=desde, hasta=hasta, sitio=sitio_id):
        v = f.get("valor")
        if v is None:
            continue
        grupos.setdefault(f.get("unidad") or "sin unidad", []).append(v)
    return [{"unidad": u, "n": len(vs), "promedio": round(sum(vs) / len(vs), 4),
             "minimo": min(vs), "maximo": max(vs)}
            for u, vs in sorted(grupos.items(), key=lambda kv: -len(kv[1]))]


def _preparar(variable: str, categoria: str) -> tuple[str | None, str, dict | None]:
    """Valida la categoría y, para flujos, el gas. Devuelve (gas, etiqueta, error)."""
    cat = (categoria or "flujos").strip().lower()
    if cat not in CATEGORIAS:
        return None, "", {
            "error": "Categoría no reconocida.",
            "categorias_validas": {
                "flujos": "flujos de gases de efecto invernadero",
                "biomasa": "carbono almacenado en biomasa",
                "cos": "carbono orgánico del suelo",
                "produccion": "producción de biomasa en gramos",
            },
        }
    if cat != "flujos":
        return None, cat, None

    gas = (variable or "").strip().upper()
    if gas not in GASES:
        return None, "", {
            "error": "Variable no reconocida para la categoría flujos.",
            "variables_validas": sorted(GASES),
        }
    return gas, gas, None


def consultar_promedio(variable: str = "", sitio: str | None = None,
                       desde: str | None = None, hasta: str | None = None,
                       categoria: str = "flujos") -> dict:
    """Promedio, mínimo y máximo por sitio y rango de fechas (AAAA-MM-DD).
    Categorías: flujos (indicar variable CO2, CH4 o N2O), biomasa, cos y
    produccion (biomasa producida, en gramos), que ignoran la variable. Si el
    grupo mezcla unidades devuelve una cifra por cada unidad, nunca una sola."""
    cat = (categoria or "flujos").strip().lower()
    gas, etiqueta, error = _preparar(variable, cat)
    if error:
        return error

    sitio_obj = None
    if sitio:
        sitio_obj, error_sitio = backend_client.resolve_sitio(sitio)
        if error_sitio:
            return error_sitio

    if sitio_obj:
        resumen = backend_client.get_resumen(
            "sitio", gas=gas, desde=desde, hasta=hasta,
            sitio=sitio_obj["id"], categoria=cat,
        )
        props = backend_client.buscar_feature(resumen, sitio_obj["id"])
        if props is None:
            return {"variable": etiqueta, "categoria": cat,
                    "sitio": sitio_obj["nombre"], "sin_datos": True}
        unidad = resumen.get("unidad")
        if not unidad and cat == "flujos":
            return {"variable": etiqueta, "categoria": cat, "sitio": sitio_obj["nombre"],
                    "n": props.get("total_muestras"),
                    "por_unidad": _por_unidad(gas, desde, hasta, sitio_obj["id"]),
                    "nota": NOTA_MEZCLA}
        return {
            "variable": etiqueta, "categoria": cat, "sitio": sitio_obj["nombre"],
            "n": props.get("total_muestras"), "promedio": props.get("promedio"),
            "minimo": props.get("minimo"), "maximo": props.get("maximo"),
            "unidad": unidad,
            "nota_unidad": "" if unidad else NOTA_SIN_UNIDAD,
        }

    if cat != "flujos":
        resumen = backend_client.get_resumen(
            "departamento", desde=desde, hasta=hasta, categoria=cat,
        )
        grupos = [f.get("properties", {}) for f in resumen.get("features", [])]
        if not grupos:
            return {"variable": etiqueta, "categoria": cat, "sin_datos": True}
        return {
            "variable": etiqueta, "categoria": cat, "agrupado_por": "departamento",
            "unidad": resumen.get("unidad"),
            "grupos": [
                {"nombre": g.get("nombre"), "n": g.get("total_muestras"),
                 "promedio": g.get("promedio")}
                for g in grupos
            ],
        }

    grupos = _por_unidad(gas, desde, hasta)
    if not grupos:
        return {"variable": etiqueta, "sitio": "todos los sitios", "sin_datos": True}
    return {"variable": etiqueta, "categoria": cat, "sitio": "todos los sitios",
            "por_unidad": grupos, "nota": NOTA_MEZCLA}


def consultar_ultima_medicion(variable: str = "", sitio: str | None = None,
                              categoria: str = "flujos") -> dict:
    """La medición más reciente de una categoría de dato, opcionalmente en un
    sitio. Categorías: flujos (indicar variable CO2, CH4 o N2O), biomasa y cos.
    La respuesta incluye la unidad de esa medición concreta."""
    cat = (categoria or "flujos").strip().lower()
    gas, etiqueta, error = _preparar(variable, cat)
    if error:
        return error

    sitio_obj = None
    if sitio:
        sitio_obj, error_sitio = backend_client.resolve_sitio(sitio)
        if error_sitio:
            return error_sitio

    if sitio_obj:
        resumen = backend_client.get_resumen("sitio", gas=gas, sitio=sitio_obj["id"], categoria=cat)
        props = backend_client.buscar_feature(resumen, sitio_obj["id"])
        if props is None or not props.get("ultima_medicion"):
            return {"variable": etiqueta, "categoria": cat, "sitio": sitio_obj["nombre"], "sin_datos": True}
        return {"variable": etiqueta, "categoria": cat, "sitio": sitio_obj["nombre"], "ultima": props["ultima_medicion"]}

    if cat != "flujos":
        resumen = backend_client.get_resumen("departamento", categoria=cat)
        grupos = [f.get("properties", {}) for f in resumen.get("features", [])]
        ultimas = [g["ultima_medicion"] for g in grupos if g.get("ultima_medicion")]
        if not ultimas:
            return {"variable": etiqueta, "categoria": cat, "sin_datos": True}
        return {"variable": etiqueta, "categoria": cat, "ultima": max(ultimas, key=lambda u: u.get("fecha") or "")}

    series = backend_client.get_series(gas=gas)
    if not series:
        return {"variable": etiqueta, "sin_datos": True}
    return {"variable": etiqueta, "categoria": cat, "ultima": max(series, key=lambda s: s["fecha"])}
