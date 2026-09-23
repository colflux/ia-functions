"""Herramienta genérica para consultar registros de campo: materia orgánica
muerta y variables ambientales, opcionalmente acotados por sitio y por fecha.

Los agregados geográficos (flujos, carbono del suelo, biomasa y producción) se
consultan con consultar_promedio, que los devuelve ya resumidos."""

import re

from mcp_server import backend_client
from mcp_server.texto import normalizar

DESCRIPCIONES = {
    "mom": "materia orgánica muerta: hojarasca y restos vegetales",
    "clima": "variables ambientales medidas junto a los flujos: temperatura del suelo y del aire, presión atmosférica, humedad relativa, punto de rocío y nivel de agua",
}


def _clave_fecha(datos: dict) -> str:
    """La clave Modelo.campo de la columna de fecha de la vista, si la hay. Se
    deduce de las columnas que describe el backend en vez de fijarla a mano,
    para que siga valiendo en vistas que se añadan más adelante."""
    for c in datos.get("columnas", []):
        if c.get("campo") == "fecha":
            return c.get("clave") or ""
    return ""



LIMITE_ESTADISTICAS = 500

IGNORAR_EN_ESTADISTICAS = ("latitud", "longitud", "id")


def _estadisticas(filas: list[dict]) -> dict:
    """Promedio, mínimo y máximo de cada columna numérica, calculados aquí y no
    por el modelo. Se le observó presentar el promedio de una muestra como si
    fuera el del conjunto, y deducir totales multiplicando promedio por conteo."""
    acumulado: dict[str, list[float]] = {}
    for fila in filas:
        for clave, valor in fila.items():
            if any(p in clave.lower() for p in IGNORAR_EN_ESTADISTICAS):
                continue
            try:
                acumulado.setdefault(clave, []).append(float(valor))
            except (TypeError, ValueError):
                continue
    return {
        clave: {"n": len(v), "promedio": round(sum(v) / len(v), 4),
                "minimo": min(v), "maximo": max(v)}
        for clave, v in acumulado.items() if len(v) > 1
    }

def _nivel_geografico(texto: str) -> str:
    """Si el texto es el nombre de una vereda, municipio o departamento, lo dice.

    Sirve para responder con precisión cuando alguien pide MOM de Cundinamarca:
    no es que el sitio no exista, es que pidió otro nivel geográfico."""
    v = normalizar(texto)
    if not v:
        return ""
    for props in backend_client.get_geo_ids():
        for campo in ("departamento", "municipio", "vereda"):
            if v == normalizar(props.get(campo)):
                return campo
    return ""


def consultar_datos_campo(tipo: str, proyecto: str = "", sitio: str = "",
                          fecha: str = "", limite: int = 5) -> dict:
    """Registros de materia orgánica muerta (tipo mom) o de variables
    ambientales (tipo clima: temperatura del suelo y del aire, presión, humedad,
    punto de rocío y nivel de agua). Se acota por sitio, dando su nombre o su
    número, y por fecha: 2021, 2021-10 o 2021-10-05. No admite rangos de fechas
    ni filtro por vereda, municipio o departamento. Úsala cuando pregunten por
    hojarasca, restos vegetales o condiciones ambientales."""
    clave = (tipo or "").strip().lower()
    if clave not in DESCRIPCIONES:
        return {"error": "Tipo no reconocido.", "tipos_validos": DESCRIPCIONES}

    nombre_proyecto = str(proyecto or "").strip()
    if not nombre_proyecto:
        resumen = []
        for p in backend_client.get_proyectos():
            total = backend_client.get_datos(p["id"], clave, limite=1).get("total", 0)
            if total:
                resumen.append({"proyecto": p["nombre"], "registros": total})
        if len(resumen) != 1:
            return {"tipo": clave, "descripcion": DESCRIPCIONES[clave],
                    "por_proyecto": resumen,
                    "siguiente_paso": "Indica el proyecto para acotar por sitio o por fecha."}
        # Con un solo proyecto con datos, preguntar cual sobra: se usa ese y se
        # ahorra una vuelta entera del agente.
        nombre_proyecto = resumen[0]["proyecto"]

    proyecto_obj, error = backend_client.resolve_proyecto(nombre_proyecto)
    if error:
        return error

    sitio_ids: list = []
    descripcion_sitio = "todos los sitios"
    if str(sitio or "").strip():
        encontrados = backend_client.get_sitios(sitio)
        if not encontrados:
            uno, _ = backend_client.resolve_sitio(sitio)
            if uno:
                encontrados = [uno]
        if not encontrados:
            nivel = _nivel_geografico(sitio)
            if nivel:
                return {"error": str(sitio) + " es un " + nivel + ", no un sitio.",
                        "sugerencia": ("Esta consulta no admite filtro por vereda, "
                                       "municipio ni departamento. Vuelve a llamar sin el "
                                       "parámetro sitio para dar el total del proyecto, y "
                                       "despues ofrece acotar por un sitio concreto.")}
            return {"error": "Ningún sitio coincide con " + str(sitio) + ".",
                    "sugerencia": "Usa listar_sitios para ver los nombres disponibles."}
        if len(encontrados) > 8:
            return {"error": "Demasiados sitios coinciden con " + str(sitio) + ".",
                    "coincidencias": len(encontrados),
                    "sugerencia": "Concreta más el nombre."}
        # Un mismo lugar puede estar registrado como varios Sitio, con series de
        # medición distintas. Se consultan todos y se suman: rendirse aquí hacía
        # que el modelo acabara dando el total del proyecto como si fuera del sitio.
        sitio_ids = [e["id"] for e in encontrados]
        nombres = sorted(set(e["nombre"] for e in encontrados))
        descripcion_sitio = ", ".join(nombres)
        if len(encontrados) > 1:
            descripcion_sitio += " (" + str(len(encontrados)) + " registros con ese nombre)"

    filtros = None
    texto_fecha = str(fecha or "").strip()
    if texto_fecha:
        if not re.fullmatch(r"[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?", texto_fecha):
            return {"error": "Formato de fecha no válido: " + texto_fecha,
                    "formatos_validos": ["2021", "2021-10", "2021-10-05"],
                    "sugerencia": "No se admiten rangos: consulta un año, un mes o un día."}
        campo = _clave_fecha(backend_client.get_datos(proyecto_obj["id"], clave, limite=1))
        if not campo:
            return {"error": "Esta vista no tiene columna de fecha para filtrar."}
        filtros = {campo: texto_fecha}

    tope = max(1, min(int(limite or 5), 25))
    objetivos = sitio_ids or [None]

    # Una sola peticion por sitio, con el tope de estadisticas, y de ahi salen tanto
    # la muestra como los calculos. Antes se pedia dos veces cada sitio: con cinco
    # registros homonimos eran diez llamadas en lugar de cinco.
    etiquetas: dict = {}
    por_sitio: list = []
    total = 0
    for sid in objetivos:
        datos = backend_client.get_datos(proyecto_obj["id"], clave,
                                         limite=LIMITE_ESTADISTICAS, sitio=sid,
                                         filtros=filtros)
        if not etiquetas:
            etiquetas = {c["clave"]: (c.get("verbose_name") or c.get("campo") or c["clave"])
                         for c in datos.get("columnas", [])}
        por_sitio.append([{etiquetas.get(k, k): v for k, v in f.items() if v not in (None, "")}
                          for f in datos.get("filas", [])])
        total += datos.get("total") or 0

    # La muestra se reparte por turnos entre los sitios. Truncar la lista concatenada
    # dejaba las filas de ejemplo casi todas del primero, aunque los totales y las
    # estadisticas si agregaran a todos.
    filas: list = []
    ronda = 0
    while len(filas) < tope and any(len(g) > ronda for g in por_sitio):
        for g in por_sitio:
            if ronda < len(g) and len(filas) < tope:
                filas.append(g[ronda])
        ronda += 1

    estadisticas = {}
    nota = ""
    if 0 < total <= LIMITE_ESTADISTICAS:
        estadisticas = _estadisticas([f for g in por_sitio for f in g])
        nota = ("Promedios, mínimos y máximos ya calculados sobre los " + str(total) +
                " registros que cumplen el filtro. Úsalos tal cual: no los recalcules "
                "a partir de las filas de ejemplo.")
    elif total > LIMITE_ESTADISTICAS:
        nota = ("Hay " + str(total) + " registros, demasiados para resumirlos aquí. "
                "Acota por sitio o por fecha si necesitas promedios.")

    return {
        "tipo": clave,
        "descripcion": DESCRIPCIONES[clave],
        "proyecto": proyecto_obj["nombre"],
        "sitio": descripcion_sitio,
        "fecha": texto_fecha or "todas las fechas",
        "total": total,
        "estadisticas": estadisticas,
        "nota": nota,
        "filas_de_ejemplo": filas,
    }
