"""Consultas agregadas del geoportal: flujos de gases (CO2/CH4/N2O), biomasa y
carbono orgánico del suelo, vía /api/geo/resumen/ y /api/geo/series/.

La materia orgánica muerta y las variables ambientales no son categorías de
este endpoint: se consultan con consultar_datos_campo (tools/datos.py)."""

from mcp_server import backend_client
from mcp_server.backend_client import GASES
from mcp_server.catalogo import CATEGORIAS
from mcp_server.texto import normalizar
from mcp_server.geografia import id_de_nivel, nivel_de


NOTA_SIN_UNIDAD = (
    "El backend no declara unidad porque el grupo mezcla varias. "
    "No supongas ninguna ni la inventes."
)


NOTA_MEZCLA = ("Este grupo mezcla unidades distintas, así que no existe un promedio único: "
               "cada unidad tiene el suyo. Muéstralos por separado y no los combines.")


def _por_unidad(gas, desde, hasta, sitio_id=None, **lugar) -> list[dict]:
    """Promedio, mínimo y máximo dentro de cada unidad, nunca entre unidades.
    `lugar` acota por vereda, municipio o departamento (sus ids)."""
    grupos: dict = {}
    for f in backend_client.get_mediciones(gas=gas, desde=desde, hasta=hasta, sitio=sitio_id, **lugar):
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
            "categorias_validas": CATEGORIAS,
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


def consultar_promedio(variable: str = "", sitio: str | int | None = None,
                       desde: str | None = None, hasta: str | None = None,
                       categoria: str = "flujos", vereda: str = "", municipio: str = "",
                       departamento: str = "") -> dict:
    """Promedio, mínimo y máximo por sitio y rango de fechas (AAAA-MM-DD).
    Categorías: flujos (indicar variable CO2, CH4 o N2O), biomasa, cos y
    produccion (biomasa producida, en gramos), que ignoran la variable. Acota por
    sitio, vereda, municipio o departamento (en flujos); biomasa, cos y
    produccion se agrupan por departamento. Si el
    grupo mezcla unidades devuelve una cifra por cada unidad, nunca una sola."""
    sitio = str(sitio).strip() if sitio is not None else ""  # el modelo a veces lo manda como número
    cat = (categoria or "flujos").strip().lower()
    gas, etiqueta, error = _preparar(variable, cat)
    if error:
        return error

    # «promedio de CO2 en Cundinamarca»: el modelo pasaba el departamento como sitio.
    if sitio and not (vereda or municipio or departamento):
        nivel = nivel_de(sitio)
        if nivel:
            vereda, municipio, departamento = ((sitio if nivel == n else "") for n in ("vereda", "municipio", "departamento"))
            sitio = ""
    lugar_pedido = next(((c, v) for c, v in (("vereda", vereda), ("municipio", municipio),
                                             ("departamento", departamento)) if str(v or "").strip()), None)

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
        aviso = ""
        if lugar_pedido and lugar_pedido[0] == "departamento":
            grupos = [g for g in grupos if normalizar(lugar_pedido[1]) in normalizar(g.get("nombre"))]
        elif lugar_pedido:
            aviso = (f"Esta categoría solo se agrupa por departamento, no por {lugar_pedido[0]}: "
                     "dilo y muestra el departamento correspondiente.")
        if not grupos:
            return {"variable": etiqueta, "categoria": cat, "sin_datos": True,
                    "lugar": lugar_pedido[1] if lugar_pedido else "todos"}
        return {
            "aviso": aviso,
            "variable": etiqueta, "categoria": cat, "agrupado_por": "departamento",
            "unidad": resumen.get("unidad"),
            "grupos": [
                {"nombre": g.get("nombre"), "n": g.get("total_muestras"),
                 "promedio": g.get("promedio")}
                for g in grupos
            ],
        }

    ambito, lugar = "todos los sitios", {}
    if lugar_pedido:
        id_lugar, nombre, err = id_de_nivel(lugar_pedido[1], lugar_pedido[0])
        if err:
            return err
        ambito, lugar = f"{lugar_pedido[0]} {nombre}", {lugar_pedido[0]: id_lugar}
    grupos = _por_unidad(gas, desde, hasta, **lugar)
    if not grupos:
        return {"variable": etiqueta, "sitio": ambito, "sin_datos": True}
    return {"variable": etiqueta, "categoria": cat, "sitio": ambito,
            "por_unidad": grupos, "nota": NOTA_MEZCLA,
            "siguiente_paso": ("Esto es el promedio de todos los sitios juntos. Para saber qué sitio "
                               "tiene el valor más alto, usa consultar_mediciones: trae el mayor de "
                               "cada unidad con su sitio.")}


def consultar_ultima_medicion(variable: str = "", sitio: str | int | None = None,
                              categoria: str = "flujos") -> dict:
    """La medición más reciente de una categoría de dato, opcionalmente en un
    sitio. Categorías: flujos (indicar variable CO2, CH4 o N2O), biomasa y cos.
    La respuesta incluye la unidad de esa medición concreta."""
    sitio = str(sitio).strip() if sitio is not None else ""  # el modelo a veces lo manda como número
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
