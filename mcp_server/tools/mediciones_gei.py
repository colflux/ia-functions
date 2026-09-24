"""Mediciones individuales de flujos de gases: los datos tal como están
guardados, con su unidad, sin promediar ni convertir nada."""

import re

from mcp_server import backend_client
from mcp_server.geografia import id_de_nivel, nivel_de
from mcp_server.backend_client import GASES

RADIO_KM = 0.5          # alrededor del sitio más cercano a una coordenada
MAX_SITIOS_PUNTO = 40   # sitios que se consultan como máximo para una coordenada

NOTA = ("Cada medición trae su propia unidad y un mismo lugar mezcla unidades "
        "distintas. Muestra cada valor con su unidad y no los sumes ni promedies.")


def _resumir(filas: list[dict]) -> dict:
    """Totales por sitio y por unidad sobre TODAS las filas, no sobre la muestra.
    Cada sitio lleva su valor máximo en cada unidad: con solo la muestra de las
    más recientes, el modelo llegó a nombrar «el sitio con más emisiones»."""
    fechas = [f.get("fecha") for f in filas if f.get("fecha")]
    por_sitio: dict = {}
    maximos: dict = {}
    unidades: dict = {}
    for f in filas:
        clave = (f.get("sitio_id"), f.get("sitio_nombre") or "sin nombre")
        unidad = f.get("unidad") or "sin unidad"
        por_sitio[clave] = por_sitio.get(clave, 0) + 1
        unidades[unidad] = unidades.get(unidad, 0) + 1
        if f.get("valor") is not None:
            del_sitio = maximos.setdefault(clave, {})
            del_sitio[unidad] = max(del_sitio.get(unidad, f["valor"]), f["valor"])
    return {
        "total_mediciones": len(filas),
        "sitios": [{"id": k[0], "nombre": k[1], "mediciones": v, "maximo_por_unidad": maximos.get(k, {})}
                   for k, v in sorted(por_sitio.items(), key=lambda kv: -kv[1])],
        "desde": min(fechas) if fechas else None,
        "hasta": max(fechas) if fechas else None,
        "por_unidad": [{"unidad": u, "mediciones": n}
                       for u, n in sorted(unidades.items(), key=lambda kv: -kv[1])],
        # El mayor valor de cada unidad y en qué sitio: el modelo llegó a comparar
        # nmol con umol «convirtiendo» mal. Aquí ya viene resuelto, unidad por unidad.
        "mayor_por_unidad": {
            u: max(({"sitio_id": k[0], "sitio": k[1], "valor": m[u]} for k, m in maximos.items() if u in m),
                   key=lambda x: x["valor"])
            for u in unidades if any(u in m for m in maximos.values())
        },
    }


def _terminos_archivos(gas: str, sitio: str, desde: str, hasta: str,
                       latitud: float | None, longitud: float | None) -> list[str]:
    """Lo que debe aparecer, tal cual, en una fila de un archivo subido."""
    terminos = [gas]
    if desde and (not hasta or hasta == desde):
        terminos.append(desde)  # un rango de fechas no se puede buscar como texto
    if latitud is not None and longitud is not None:
        terminos += [str(latitud), str(longitud)]
    elif sitio and not str(sitio).strip().isdigit():
        terminos.append(str(sitio).strip())
    return terminos


def consultar_mediciones(gas: str = "CO2", sitio: str | int = "", vereda: str = "",
                         municipio: str = "", departamento: str = "",
                         desde: str = "", hasta: str = "", limite: int = 10,
                         latitud: float | None = None, longitud: float | None = None) -> dict:
    """Mediciones individuales de un gas (CO2, CH4, N2O) tal como están guardadas:
    fecha, valor, unidad y sitio. Acota por sitio (nombre o id), vereda, municipio,
    departamento y fechas AAAA-MM-DD. Con latitud y longitud usa los sitios mas
    cercanos a ese punto. No promedia. Dice qué sitio tiene el valor más alto en
    cada unidad: úsala para «dónde hay más» o «qué sitio emite más»."""
    sitio = str(sitio).strip() if sitio is not None else ""  # el modelo a veces lo manda como número
    if sitio and not (vereda or municipio or departamento) and not re.search(r"\d", sitio):
        # «Guatavita» como sitio: es un municipio y hay 76 sitios ahí; se consulta
        # como municipio en vez de responder que coinciden demasiados.
        nivel = nivel_de(sitio)
        if nivel == "vereda":
            vereda, sitio = sitio, ""
        elif nivel == "municipio":
            municipio, sitio = sitio, ""
        elif nivel == "departamento":
            departamento, sitio = sitio, ""
    g = (gas or "CO2").strip().upper()
    if g not in GASES:
        return {"error": "Gas no reconocido.", "gases_validos": sorted(GASES)}
    tope = max(1, min(int(limite or 10), 30))
    filas: list[dict] = []
    ambito = "todos los sitios"
    aviso_radio = ""
    if latitud is not None and longitud is not None:
        # Varios registros de Sitio comparten ubicacion (son series de medicion
        # distintas del mismo lugar), asi que se toman todos los que caen a menos
        # de medio kilometro del mas cercano, no solo el primero.
        cercanos = backend_client.sitios_cerca(latitud, longitud, limite=None)
        if not cercanos:
            return {"error": "Ningún sitio tiene coordenadas registradas."}
        tope_km = cercanos[0]["distancia_km"] + RADIO_KM
        cercanos = [c for c in cercanos if c["distancia_km"] <= tope_km]
        if len(cercanos) > MAX_SITIOS_PUNTO:
            aviso_radio = (f"Hay {len(cercanos)} sitios a menos de {RADIO_KM} km del punto; se consultaron "
                           f"los {MAX_SITIOS_PUNTO} más cercanos. Dilo en la respuesta.")
            cercanos = cercanos[:MAX_SITIOS_PUNTO]
        for c in cercanos:
            filas.extend(backend_client.get_mediciones(gas=g, desde=desde or None,
                                                       hasta=hasta or None, sitio=c["id"]))
        nombres = sorted(set(c["nombre"] for c in cercanos))
        ambito = ("punto " + str(latitud) + ", " + str(longitud) + ": "
                  + ", ".join(nombres) + " (a " + str(cercanos[0]["distancia_km"]) + " km)")
    elif sitio:
        # «254» o «sitio 254» es un número de sitio, no parte de un nombre.
        numero = re.fullmatch(r"(?:sitio\s*)?(\d+)", str(sitio).strip().lower())
        if numero:
            encontrados = [s for s in backend_client.get_sitios() if s["id"] == int(numero.group(1))]
        else:
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
            lugares = sorted({", ".join(x for x in (s.get("municipio"), s.get("departamento")) if x) or "sin lugar"
                              for s in encontrados})
            # «Palacios» son tres sitios, en Cundinamarca y en Caldas: sumarlos sin
            # decirlo hace pasar por un solo lugar lo que son varios.
            aviso_radio = (f"Hay {len(encontrados)} sitios llamados así ({'; '.join(lugares)}) y se sumaron. "
                           "Dilo en la respuesta y ofrece acotar por municipio.")
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
                "nota": "No hay mediciones de ese gas con esos criterios en la plataforma.",
                # El orquestador ejecuta esta consulta de una vez: el dato puede estar
                # en un archivo subido desde el chat, que no pasa por el backend.
                "consultar_tambien": {
                    "herramienta": "consultar_archivos_subidos",
                    "argumentos": {"terminos": _terminos_archivos(g, sitio, desde, hasta, latitud, longitud)},
                }}
    salida = {"gas": g, "ambito": ambito}
    salida.update(_resumir(filas))
    salida["mediciones"] = [
        {"fecha": f.get("fecha"), "valor": f.get("valor"), "unidad": f.get("unidad"),
         "sitio": f.get("sitio_nombre"), "sitio_id": f.get("sitio_id")}
        for f in sorted(filas, key=lambda x: x.get("fecha") or "", reverse=True)[:tope]
    ]
    salida["nota"] = NOTA
    if len(filas) > tope:
        salida["nota_muestra"] = (f"'mediciones' son solo las {tope} más recientes de {len(filas)}: no sirven "
                                  "para decir cuál es el mayor, el menor ni el promedio. Para comparar sitios "
                                  "usa 'mayor_por_unidad' o 'maximo_por_unidad' de cada sitio, compara solo "
                                  "dentro de una misma unidad y nunca conviertas entre unidades.")
    if aviso_radio:
        salida["aviso"] = aviso_radio
    return salida
