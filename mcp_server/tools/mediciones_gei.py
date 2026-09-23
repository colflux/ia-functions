"""Mediciones individuales de flujos de gases: los datos tal como están
guardados, con su unidad, sin promediar ni convertir nada."""

from mcp_server import backend_client
from mcp_server.geografia import id_de_nivel
from mcp_server.backend_client import GASES

NOTA = ("Cada medición trae su propia unidad y un mismo lugar mezcla unidades "
        "distintas. Muestra cada valor con su unidad y no los sumes ni promedies.")


def _resumir(filas: list[dict]) -> dict:
    fechas = [f.get("fecha") for f in filas if f.get("fecha")]
    por_sitio: dict = {}
    for f in filas:
        clave = (f.get("sitio_id"), f.get("sitio_nombre") or "sin nombre")
        por_sitio[clave] = por_sitio.get(clave, 0) + 1
    unidades: dict = {}
    for f in filas:
        clave = f.get("unidad") or "sin unidad"
        unidades[clave] = unidades.get(clave, 0) + 1
    return {
        "total_mediciones": len(filas),
        "sitios": [{"id": k[0], "nombre": k[1], "mediciones": v}
                   for k, v in sorted(por_sitio.items(), key=lambda kv: -kv[1])],
        "desde": min(fechas) if fechas else None,
        "hasta": max(fechas) if fechas else None,
        "por_unidad": [{"unidad": u, "mediciones": n}
                       for u, n in sorted(unidades.items(), key=lambda kv: -kv[1])],
    }


def consultar_mediciones(gas: str = "CO2", sitio: str = "", vereda: str = "",
                         municipio: str = "", departamento: str = "",
                         desde: str = "", hasta: str = "", limite: int = 10,
                         latitud: float | None = None, longitud: float | None = None) -> dict:
    """Mediciones individuales de un gas (CO2, CH4, N2O) tal como están guardadas:
    fecha, valor, unidad y sitio. Acota por sitio (nombre o id), vereda, municipio,
    departamento y fechas AAAA-MM-DD. Con latitud y longitud usa los sitios mas
    cercanos a ese punto. No promedia."""
    g = (gas or "CO2").strip().upper()
    if g not in GASES:
        return {"error": "Gas no reconocido.", "gases_validos": sorted(GASES)}
    tope = max(1, min(int(limite or 10), 30))
    filas: list[dict] = []
    ambito = "todos los sitios"
    if latitud is not None and longitud is not None:
        # Varios registros de Sitio comparten ubicacion (son series de medicion
        # distintas del mismo lugar), asi que se toman todos los que caen a menos
        # de medio kilometro del mas cercano, no solo el primero.
        cercanos = backend_client.sitios_cerca(latitud, longitud, 8)
        if not cercanos:
            return {"error": "Ningún sitio tiene coordenadas registradas."}
        tope_km = cercanos[0]["distancia_km"] + 0.5
        cercanos = [c for c in cercanos if c["distancia_km"] <= tope_km]
        for c in cercanos:
            filas.extend(backend_client.get_mediciones(gas=g, desde=desde or None,
                                                       hasta=hasta or None, sitio=c["id"]))
        nombres = sorted(set(c["nombre"] for c in cercanos))
        ambito = ("punto " + str(latitud) + ", " + str(longitud) + ": "
                  + ", ".join(nombres) + " (a " + str(cercanos[0]["distancia_km"]) + " km)")
    elif sitio:
        encontrados = backend_client.get_sitios(sitio)
        if not encontrados:
            return {"error": "Ningún sitio coincide con " + str(sitio),
                    "sugerencia": "Usa listar_sitios para ver los nombres."}
        if len(encontrados) > 6:
            return {"error": "Demasiados sitios coinciden con " + str(sitio),
                    "coincidencias": len(encontrados),
                    "sugerencia": "Acota por vereda o municipio."}
        for s in encontrados:
            filas.extend(backend_client.get_mediciones(gas=g, desde=desde or None,
                                                       hasta=hasta or None, sitio=s["id"]))
        nombres = sorted(set(s["nombre"] for s in encontrados))
        ambito = "sitio " + ", ".join(nombres)
        if len(encontrados) > 1:
            ambito += " (" + str(len(encontrados)) + " registros con ese nombre)"
    else:
        campo, valor = "", ""
        for nivel, dado in (("vereda", vereda), ("municipio", municipio),
                            ("departamento", departamento)):
            if dado:
                campo, valor = nivel, dado
                break
        params = {}
        if campo:
            id_nivel, nombre, err = id_de_nivel(valor, campo)
            if err:
                return err
            params[campo] = id_nivel
            ambito = campo + " " + str(nombre)
        filas = backend_client.get_mediciones(gas=g, desde=desde or None,
                                              hasta=hasta or None, **params)
    if not filas:
        return {"gas": g, "ambito": ambito, "sin_datos": True,
                "nota": "No hay mediciones de ese gas con esos criterios."}
    salida = {"gas": g, "ambito": ambito}
    salida.update(_resumir(filas))
    salida["mediciones"] = [
        {"fecha": f.get("fecha"), "valor": f.get("valor"), "unidad": f.get("unidad"),
         "sitio": f.get("sitio_nombre"), "sitio_id": f.get("sitio_id")}
        for f in sorted(filas, key=lambda x: x.get("fecha") or "", reverse=True)[:tope]
    ]
    salida["nota"] = NOTA
    return salida
