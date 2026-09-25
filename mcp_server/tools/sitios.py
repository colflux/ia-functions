"""Exploración de los sitios de monitoreo por niveles geográficos, para que el
asistente no tenga que volcar los 113 sitios de una vez: primero departamentos,
luego municipios, luego veredas y al final los sitios concretos."""

import logging

from mcp_server import backend_client
from mcp_server.backend_client import GASES
from mcp_server.texto import normalizar

logger = logging.getLogger(__name__)

NOTA_DATOS = ("'sitios'/'total_sitios' son los sitios registrados; 'con_mediciones' son los que "
              "tienen mediciones de gases con valor (CO2, CH4, N2O). Si preguntan de qué sitios hay "
              "datos, responde con 'con_mediciones' y no con el total de registrados. No menciones "
              "tablas ni listas internas: nombra los sitios o di cuántos son.")


def _ids_con_mediciones() -> set | None:
    """Ids de los sitios con al menos una medición de gases. Un sitio puede estar
    registrado sin datos (por ejemplo si se borraron sus cargas), y contarlo como
    «con datos» confunde. None si el backend no respondió: mejor no afirmar nada."""
    ids: set = set()
    try:
        for gas in GASES:
            resumen = backend_client.get_resumen("sitio", gas=gas)
            # Solo cuentan los sitios con al menos un valor: hay sitios con registros
            # de medición vacíos (valor nulo), y el promedio los ignora.
            ids.update((f.get("properties") or {}).get("id") for f in resumen.get("features", [])
                       if (f.get("properties") or {}).get("promedio") is not None)
    except Exception:
        logger.exception("no se pudieron leer los sitios con mediciones")
        return None
    ids.discard(None)
    return ids


def _agrupar(sitios: list[dict], campo: str, con_datos: set | None = None) -> list[dict]:
    """Cuenta cuántos sitios hay por cada valor del campo, de mayor a menor, y
    cuántos de ellos tienen mediciones."""
    conteo: dict[str, list[int]] = {}
    for s in sitios:
        clave = (s.get(campo) or "").strip() or "sin dato"
        par = conteo.setdefault(clave, [0, 0])
        par[0] += 1
        par[1] += 1 if con_datos is not None and s.get("id") in con_datos else 0
    salida = []
    for k, (total, datos) in sorted(conteo.items(), key=lambda kv: -kv[1][0]):
        fila = {"nombre": k, "sitios": total}
        if con_datos is not None:
            fila["con_mediciones"] = datos
        salida.append(fila)
    return salida


def _con_datos(salida: dict, sitios: list[dict], con_datos: set | None) -> dict:
    if con_datos is not None:
        salida["total_con_mediciones"] = sum(1 for s in sitios if s.get("id") in con_datos)
        salida["nota_datos"] = NOTA_DATOS
    return salida


def _acotar(sitios: list[dict], campo: str, valor: str) -> list[dict]:
    """Filtra por coincidencia parcial, ignorando mayúsculas, tildes y espacios."""
    v = normalizar(valor)
    if not v:
        return sitios
    return [s for s in sitios if v in normalizar(s.get(campo))]


def listar_sitios(departamento: str = "", municipio: str = "", vereda: str = "",
                  filtro: str = "", limite: int = 10,
                  latitud: float | None = None, longitud: float | None = None) -> dict:
    """Sitios de monitoreo por niveles: sin argumentos da los departamentos con su
    recuento; con departamento da municipios; con municipio da veredas; con vereda
    da los sitios. filtro busca por nombre, vereda, municipio o id y salta directo
    a los sitios. latitud y longitud dan los más cercanos a un punto."""
    if latitud is not None and longitud is not None:
        cercanos = backend_client.sitios_cerca(latitud, longitud, max(1, min(int(limite or 5), 20)))
        if not cercanos:
            return {"sin_resultados": True, "mensaje": "Ningún sitio tiene coordenadas."}
        return {"nivel": "sitio", "busqueda": "por coordenada", "sitios": cercanos, "nota": "Varios registros pueden compartir ubicación: son series de medición distintas del mismo lugar, no duplicados."}
    if filtro and str(filtro).strip().isdigit():
        uno = [s for s in backend_client.get_sitios() if s["id"] == int(str(filtro).strip())]
        if uno:
            return {"nivel": "sitio", "total_sitios": 1, "mostrados": 1, "sitios": uno}
    sitios = backend_client.get_sitios(filtro or None)
    sitios = _acotar(sitios, "departamento", departamento)
    sitios = _acotar(sitios, "municipio", municipio)
    sitios = _acotar(sitios, "vereda", vereda)

    if not sitios:
        return {"sin_resultados": True, "mensaje": "Ningún sitio coincide con esos criterios."}
    con_datos = _ids_con_mediciones()

    if not filtro and not departamento and len(_agrupar(sitios, "departamento")) > 1:
        return _con_datos({
            "nivel": "departamento", "total_sitios": len(sitios),
            "elige_uno_de": _agrupar(sitios, "departamento", con_datos),
            "siguiente_paso": "Pregunta a la persona qué departamento le interesa y vuelve a llamar a listar_sitios indicándolo.",
        }, sitios, con_datos)

    if not filtro and not municipio and len(_agrupar(sitios, "municipio")) > 1:
        return _con_datos({
            "nivel": "municipio", "departamento": departamento,
            "total_sitios": len(sitios),
            "elige_uno_de": _agrupar(sitios, "municipio", con_datos),
            "siguiente_paso": "Pregunta qué municipio y vuelve a llamar indicando municipio.",
        }, sitios, con_datos)

    if not filtro and not vereda and len(sitios) > limite and len(_agrupar(sitios, "vereda")) > 1:
        return _con_datos({
            "nivel": "vereda", "municipio": municipio,
            "total_sitios": len(sitios),
            "elige_uno_de": _agrupar(sitios, "vereda", con_datos),
            "siguiente_paso": "Pregunta qué vereda y vuelve a llamar indicando vereda.",
        }, sitios, con_datos)

    tope = max(1, min(int(limite or 10), 50))
    mostrados = min(len(sitios), tope)
    elegidos = sitios[:tope]
    if con_datos is not None:
        elegidos = [dict(s, con_mediciones=s.get("id") in con_datos) for s in elegidos]
    return _con_datos({
        "nivel": "sitio", "total_sitios": len(sitios), "mostrados": mostrados,
        "resumen": f"mostrando {mostrados} de {len(sitios)} sitios",
        "sitios": elegidos,
    }, sitios, con_datos)
