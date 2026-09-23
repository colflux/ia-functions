"""Resolución de nombres geográficos: vereda, municipio y departamento.

Antes vivía repartida en dos herramientas que ya habían divergido. Ahora las dos
funciones comparten la fuente —los identificadores del resumen por sitio— y la
normalización. Difieren a propósito en cómo comparan: id_de_nivel busca el lugar
que la persona quiso decir y admite coincidencias parciales; nivel_de solo
confirma de qué tipo es un nombre, y por eso exige coincidencia exacta.

La resolución de sitios sigue en backend_client.resolve_sitio: un sitio es otra
entidad, con otra fuente y otras reglas."""

from mcp_server import backend_client
from mcp_server.texto import normalizar

NIVELES = ("departamento", "municipio", "vereda")


def id_de_nivel(valor: str, campo: str):
    """El id del nivel geográfico que pidió la persona. Devuelve (id, nombre, error).

    Los nombres de vereda se repiten entre municipios, así que quedarse con la
    primera coincidencia daría datos de otro lugar sin avisar: si hay varias, se
    pide concretar."""
    v = normalizar(valor)
    if not v:
        return None, None, None
    encontrados: dict = {}
    for props in backend_client.get_geo_ids():
        nombre = props.get(campo)
        if v in normalizar(nombre):
            encontrados[props.get(campo + "_id")] = nombre
    if not encontrados:
        return None, None, {
            "error": "No encontré " + campo + " " + str(valor),
            "sugerencia": ("Puede estar escrito de otra forma, o existir sin tener "
                           "mediciones de ese gas. Usa listar_sitios para verlo."),
        }
    if len(encontrados) > 1:
        return None, None, {
            "error": "Varias opciones de " + campo + " coinciden con " + str(valor),
            "candidatos": sorted(set(encontrados.values())),
            "sugerencia": "Pregunta a la persona cuál de ellas quiere.",
        }
    id_nivel, nombre = next(iter(encontrados.items()))
    return id_nivel, nombre, None


def nivel_de(texto: str) -> str:
    """Si el texto es exactamente el nombre de un departamento, municipio o vereda,
    dice cuál. Sirve para contestar con precisión cuando alguien pasa un
    departamento donde se esperaba un sitio."""
    v = normalizar(texto)
    if not v:
        return ""
    for props in backend_client.get_geo_ids():
        for campo in NIVELES:
            if v == normalizar(props.get(campo)):
                return campo
    return ""
