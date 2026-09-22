"""Exploración de los sitios de monitoreo por niveles geográficos, para que el
asistente no tenga que volcar los 113 sitios de una vez: primero departamentos,
luego municipios, luego veredas y al final los sitios concretos."""

from mcp_server import backend_client
from mcp_server.texto import normalizar


def _agrupar(sitios: list[dict], campo: str) -> list[dict]:
    """Cuenta cuántos sitios hay por cada valor del campo, de mayor a menor."""
    conteo: dict[str, int] = {}
    for s in sitios:
        clave = (s.get(campo) or "").strip() or "sin dato"
        conteo[clave] = conteo.get(clave, 0) + 1
    return [{"nombre": k, "sitios": v} for k, v in sorted(conteo.items(), key=lambda kv: -kv[1])]


def _acotar(sitios: list[dict], campo: str, valor: str) -> list[dict]:
    """Filtra por coincidencia parcial, ignorando mayúsculas, tildes y espacios."""
    v = normalizar(valor)
    if not v:
        return sitios
    return [s for s in sitios if v in normalizar(s.get(campo))]


def listar_sitios(departamento: str = "", municipio: str = "", vereda: str = "",
                  filtro: str = "", limite: int = 10,
                  latitud: float = 0.0, longitud: float = 0.0) -> dict:
    """Sitios de monitoreo por niveles: sin argumentos da los departamentos con su
    recuento; con departamento da municipios; con municipio da veredas; con vereda
    da los sitios. filtro busca por nombre, vereda, municipio o id y salta directo
    a los sitios. latitud y longitud dan los más cercanos a un punto."""
    if latitud and longitud:
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

    if not filtro and not departamento and len(_agrupar(sitios, "departamento")) > 1:
        return {
            "nivel": "departamento", "total_sitios": len(sitios),
            "elige_uno_de": _agrupar(sitios, "departamento"),
            "siguiente_paso": "Pregunta a la persona qué departamento le interesa y vuelve a llamar a listar_sitios indicándolo.",
        }

    if not filtro and not municipio and len(_agrupar(sitios, "municipio")) > 1:
        return {
            "nivel": "municipio", "departamento": departamento,
            "total_sitios": len(sitios),
            "elige_uno_de": _agrupar(sitios, "municipio"),
            "siguiente_paso": "Pregunta qué municipio y vuelve a llamar indicando municipio.",
        }

    if not filtro and not vereda and len(sitios) > limite and len(_agrupar(sitios, "vereda")) > 1:
        return {
            "nivel": "vereda", "municipio": municipio,
            "total_sitios": len(sitios),
            "elige_uno_de": _agrupar(sitios, "vereda"),
            "siguiente_paso": "Pregunta qué vereda y vuelve a llamar indicando vereda.",
        }

    tope = max(1, min(int(limite or 10), 50))
    mostrados = min(len(sitios), tope)
    return {
        "nivel": "sitio", "total_sitios": len(sitios), "mostrados": mostrados,
        "resumen": f"mostrando {mostrados} de {len(sitios)} sitios",
        "sitios": sitios[:tope],
    }
