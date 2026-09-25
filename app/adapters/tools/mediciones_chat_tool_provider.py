"""Mediciones que una persona dicta en el chat («hoy medí 3,2 µmol de CO2 en el
sitio 105»).

Se guardan en la base del asistente (tabla mediciones_chat), marcadas como
pendientes de validación: no entran a la base de la plataforma ni aparecen en
mapas hasta que, en el futuro, pasen por el ETL. Mientras tanto el chat las
puede consultar y exportar.

Mínimos, los mismos que para subir datos de flujos: sitio, fecha, gas, valor,
unidad y condición de luz. Las coordenadas solo se piden si el sitio no existe
en la plataforma. Registrar exige sesión con nivel reportador, verificada con el
backend. El guardado pasa por una confirmación explícita («confirmo»): la
herramienta que escribe no se le ofrece al modelo, solo la ejecuta el
orquestador cuando la persona confirma.
"""
import logging
import re
import unicodedata
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any

from app.db.session import get_connection
from app.domain.models import ToolSpec
from app.domain.ports.data_model import DataModelCatalog
from app.domain.ports.tool_provider import ToolProvider
from app.domain.ports.user_directory import UserDirectory
from app.domain.services.contexto import autorizacion_actual
from app.domain.services.lugar import LugarInvalido, coordenadas

logger = logging.getLogger("uvicorn.error")

NIVELES = ["ciudadano", "investigador", "reportador", "admin"]
MAX_FILAS_CONSULTA = 30
NOTA_ORIGEN = ("Mediciones dictadas en el chat: están pendientes de validación por el ETL y todavía "
               "no aparecen en los mapas ni en el visor. Dilo al mostrarlas.")

GASES = {"co2": "CO2", "dioxido": "CO2", "carbono": "CO2", "ch4": "CH4", "metano": "CH4",
         "n2o": "N2O", "oxidonitroso": "N2O", "nitroso": "N2O"}
UNIDADES = (("nmol", "nmol_m2_s"), ("umol", "umol_m2_s"), ("micromol", "umol_m2_s"), ("µmol", "umol_m2_s"),
            ("g_m2_h", "g_m2_h"), ("gm2h", "g_m2_h"), ("g/m2/h", "g_m2_h"), ("gramo", "g_m2_h"))
LUZ = {"dia": "dia", "diurna": "dia", "diurno": "dia", "noche": "noche", "nocturna": "noche", "nocturno": "noche"}

PREGUNTAS = {
    "sitio": "¿En qué sitio fue la medición? (nombre o número del sitio)",
    "fecha": "¿Qué día se tomó? (por ejemplo 2026-09-24, o «hoy»)",
    "gas": "¿Qué gas se midió: CO2, CH4 o N2O?",
    "valor": "¿Cuál fue el valor medido?",
    "unidad": "¿En qué unidad está el valor: µmol/m²/s, nmol/m²/s o g/m²/h?",
    "condicion_luz": "¿La medición fue de día o de noche?",
    "coordenadas": "Ese sitio no está registrado en la plataforma: ¿cuáles son sus coordenadas? (por ejemplo 4.9136, -73.7363)",
}


def _plano(texto: Any) -> str:
    base = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9/_µ]", "", base)


def _gas(valor: Any) -> str | None:
    v = _plano(valor).replace("₂", "2").replace("₄", "4")
    return next((g for k, g in GASES.items() if k in v), None)


def _unidad(valor: Any) -> str | None:
    v = str(valor or "").lower().replace(" ", "").replace("²", "2").replace("⁻", "-")
    return next((u for k, u in UNIDADES if k in v), None)


def _numero(valor: Any) -> float | None:
    if isinstance(valor, (int, float)):
        return float(valor)
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(valor or ""))
    return float(m.group(0).replace(",", ".")) if m else None


def _fecha(valor: Any) -> tuple[date | None, str]:
    """(fecha, problema). Acepta AAAA-MM-DD, DD/MM/AAAA, «hoy» y «ayer»."""
    v = _plano(valor)
    if not v:
        return None, ""
    hoy = datetime.now(ZoneInfo("America/Bogota")).date()  # «hoy» de quien mide, no del servidor
    if v in ("hoy", "today"):
        return hoy, ""
    if v == "ayer":
        return hoy - timedelta(days=1), ""
    texto = str(valor).strip()
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            f = datetime.strptime(texto, formato).date()
            break
        except ValueError:
            continue
    else:
        return None, "No entendí la fecha; escríbela como 2026-09-24."
    if f > hoy:
        return None, "La fecha es futura; revisa el día de la medición."
    return f, ""


class MedicionesChatToolProvider(ToolProvider):
    # Herramientas que el orquestador puede ejecutar pero el modelo no ve.
    ocultas = ["guardar_medicion"]

    def __init__(self, usuarios: UserDirectory, modelo: DataModelCatalog) -> None:
        self._usuarios = usuarios
        self._modelo = modelo

    def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="registrar_medicion",
                description=(
                    "Registra una medición de gases que la persona dicta en el chat, por ejemplo "
                    "«hoy medí 3,2 umol de CO2 en el sitio 105». Pásale todo lo que la persona haya "
                    "dicho (en esta y en las respuestas anteriores). Si falta algo devuelve qué "
                    "preguntar; si está completo prepara un resumen para que la persona lo confirme."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "sitio": {"type": "string", "description": "Nombre o número del sitio"},
                        "fecha": {"type": "string", "description": "AAAA-MM-DD, o «hoy» / «ayer»"},
                        "gas": {"type": "string", "description": "CO2, CH4 o N2O"},
                        "valor": {"type": "number"},
                        "unidad": {"type": "string", "description": "umol_m2_s, nmol_m2_s o g_m2_h"},
                        "condicion_luz": {"type": "string", "description": "dia o noche"},
                        "coordenadas": {"type": "string", "description": "Solo si el sitio es nuevo: «lat, lon»"},
                    },
                },
            ),
            ToolSpec(
                name="consultar_mediciones_chat",
                description=(
                    "Mediciones que las personas han dictado en el chat (pendientes de validación por "
                    "el ETL, no están en los mapas). Filtra por sitio y gas. Con exportar=true las "
                    "entrega completas para el Excel."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "sitio": {"type": "string"},
                        "gas": {"type": "string"},
                        "exportar": {"type": "boolean"},
                    },
                },
            ),
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "registrar_medicion":
            return self._registrar(arguments)
        if name == "guardar_medicion":
            return self._guardar(arguments)
        if name == "consultar_mediciones_chat":
            return self._consultar(arguments)
        return {"error": f"Herramienta desconocida: {name}"}

    # ── permisos ──────────────────────────────────────────────────────────
    def _usuario(self) -> tuple[dict | None, str]:
        autorizacion = autorizacion_actual.get()
        if not autorizacion:
            return None, "Para registrar mediciones inicia sesión con una cuenta de nivel reportador."
        try:
            usuario = self._usuarios.usuario_por_token(autorizacion)
        except Exception:
            logger.exception("MEDICION no se pudo verificar la sesión")
            return None, "No se pudo verificar tu sesión en este momento. Intenta de nuevo más tarde."
        if usuario is None:
            return None, "Tu sesión no es válida o expiró. Vuelve a iniciar sesión."
        nivel = usuario.get("nivel")
        if nivel not in NIVELES or NIVELES.index(nivel) < NIVELES.index("reportador"):
            return None, ("Para registrar mediciones necesitas nivel reportador. Puedes solicitarlo "
                          "a un administrador de la plataforma.")
        return usuario, ""

    # ── sitios ────────────────────────────────────────────────────────────
    def _buscar_sitio(self, texto: str) -> tuple[dict | None, list[dict]]:
        """(sitio único, candidatos si hay varios)."""
        sitios = self._modelo.sitios()
        numero = re.fullmatch(r"(?:sitio\s*)?(\d+)", str(texto).strip().lower())
        if numero:
            uno = [s for s in sitios if s["id"] == int(numero.group(1))]
            return (uno[0] if uno else None), []
        buscado = _plano(texto)
        exactos = [s for s in sitios if buscado and _plano(s.get("nombre")) == buscado]
        parecidos = exactos or [s for s in sitios if len(buscado) >= 4 and buscado in _plano(s.get("nombre"))]
        if len(parecidos) == 1:
            return parecidos[0], []
        return None, parecidos

    @staticmethod
    def _etiqueta(s: dict) -> str:
        nombre = s.get("nombre") or f"Sitio {s['id']}"
        lugar = ", ".join(p.title() if p.isupper() else p for p in (s.get("vereda"), s.get("municipio")) if p)
        return f"{nombre} (id {s['id']}{', ' + lugar if lugar else ''})"

    # ── registrar: validar y proponer ─────────────────────────────────────
    def _registrar(self, a: dict[str, Any]) -> dict[str, Any]:
        usuario, problema = self._usuario()
        if usuario is None:
            return {"error": problema, "nota": "Dile esto a la persona tal cual; no sigas pidiendo datos."}

        faltan: list[str] = []
        problemas: list[str] = []
        recibido: dict[str, Any] = {}

        gas = _gas(a.get("gas"))
        (recibido.__setitem__("gas", gas) if gas else faltan.append("gas"))
        valor = _numero(a.get("valor"))
        (recibido.__setitem__("valor", valor) if valor is not None else faltan.append("valor"))
        unidad = _unidad(a.get("unidad"))
        if unidad:
            recibido["unidad"] = unidad
        elif a.get("unidad"):
            problemas.append("Unidad no reconocida: usa µmol/m²/s, nmol/m²/s o g/m²/h.")
        else:
            faltan.append("unidad")
        luz = LUZ.get(_plano(a.get("condicion_luz")).replace("de", ""))
        (recibido.__setitem__("condicion_luz", luz) if luz else faltan.append("condicion_luz"))
        fecha, prob_fecha = _fecha(a.get("fecha"))
        if fecha:
            recibido["fecha"] = fecha.isoformat()
        elif prob_fecha:
            problemas.append(prob_fecha)
        else:
            faltan.append("fecha")

        sitio_obj = None
        if not str(a.get("sitio") or "").strip():
            faltan.append("sitio")
        else:
            sitio_obj, candidatos = self._buscar_sitio(a["sitio"])
            if sitio_obj:
                recibido["sitio"] = self._etiqueta(sitio_obj)
            elif candidatos:
                problemas.append("Hay varios sitios con ese nombre; pregunta cuál: "
                                 + "; ".join(self._etiqueta(s) for s in candidatos[:8]))
            else:
                recibido["sitio"] = f"{a['sitio']} (sitio nuevo, no registrado en la plataforma)"
                coords, invalidas = None, False
                try:
                    coords = coordenadas(str(a.get("coordenadas") or ""))
                except LugarInvalido as exc:
                    invalidas = True
                    problemas.append(str(exc))
                if coords:
                    recibido["coordenadas"] = f"{coords[0]}, {coords[1]}"
                elif not invalidas:
                    faltan.append("coordenadas")

        if faltan or problemas:
            return {
                "completa": False,
                "recibido": recibido,
                "faltan": [PREGUNTAS[f] for f in faltan],
                "problemas": problemas,
                "nota": ("Pregunta a la persona solo lo que falta o está mal, en un único mensaje breve. "
                         "Cuando responda, vuelve a llamar a registrar_medicion con TODOS los datos, "
                         "los anteriores y los nuevos."),
            }

        propuesta = {
            "sitio_id": sitio_obj["id"] if sitio_obj else None,
            "sitio_nombre": self._etiqueta(sitio_obj) if sitio_obj else str(a["sitio"]).strip(),
            "latitud": sitio_obj.get("latitud") if sitio_obj else coords[0],
            "longitud": sitio_obj.get("longitud") if sitio_obj else coords[1],
            "fecha": fecha.isoformat(), "gas": gas, "valor": valor, "unidad": unidad,
            "condicion_luz": luz,
        }
        resumen = (f"{gas} = {valor:g} {unidad} en {propuesta['sitio_nombre']}, el {fecha.isoformat()}, "
                   f"de {'día' if luz == 'dia' else 'noche'}")
        return {
            "completa": True,
            "resumen": resumen,
            "nota": ("Muestra este resumen y pide a la persona que escriba «confirmo» para guardarlo, "
                     "o que corrija lo que esté mal. Aclara que quedará pendiente de validación por el ETL."),
            "pending_confirmation": propuesta,
            "confirmar_con": "guardar_medicion",
        }

    # ── guardar: solo tras «confirmo» ─────────────────────────────────────
    def _guardar(self, p: dict[str, Any]) -> dict[str, Any]:
        usuario, problema = self._usuario()
        if usuario is None:
            return {"error": problema + " No se guardó nada."}
        quien = str(usuario.get("id") or "")
        nombre = usuario.get("nombre") or usuario.get("username") or usuario.get("email") or ""
        with get_connection() as conn:
            repetida = conn.execute(
                "SELECT id FROM mediciones_chat WHERE sitio_nombre = %s AND fecha = %s AND gas = %s "
                "AND valor = %s AND unidad = %s AND condicion_luz = %s AND usuario_id = %s",
                (p["sitio_nombre"], p["fecha"], p["gas"], p["valor"], p["unidad"], p["condicion_luz"], quien),
            ).fetchone()
            if repetida:
                return {"mensaje": f"Esa medición ya estaba guardada (registro {repetida[0]}). No se guardó de nuevo."}
            fila = conn.execute(
                "INSERT INTO mediciones_chat (sitio_id, sitio_nombre, latitud, longitud, fecha, gas, valor, "
                "unidad, condicion_luz, usuario_id, usuario_nombre) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (p.get("sitio_id"), p["sitio_nombre"], p.get("latitud"), p.get("longitud"), p["fecha"],
                 p["gas"], p["valor"], p["unidad"], p["condicion_luz"], quien, nombre),
            ).fetchone()
        logger.info("MEDICION guardada id=%s usuario=%s %s", fila[0], quien, p)
        return {"mensaje": (f"Listo, guardé la medición (registro {fila[0]}): {p['gas']} = {p['valor']:g} "
                            f"{p['unidad']} en {p['sitio_nombre']}, el {p['fecha']}. Queda pendiente de "
                            "validación por el ETL; ya la puedes consultar aquí en el chat.")}

    # ── consultar ─────────────────────────────────────────────────────────
    def _consultar(self, a: dict[str, Any]) -> dict[str, Any]:
        condiciones, valores = [], []
        gas = _gas(a.get("gas")) if a.get("gas") else None
        if gas:
            condiciones.append("gas = %s")
            valores.append(gas)
        sitio = str(a.get("sitio") or "").strip()
        if sitio:
            numero = re.fullmatch(r"(?:sitio\s*)?(\d+)", sitio.lower())
            if numero:
                condiciones.append("sitio_id = %s")
                valores.append(int(numero.group(1)))
            else:
                condiciones.append("sitio_nombre ILIKE %s")
                valores.append(f"%{sitio}%")
        donde = ("WHERE " + " AND ".join(condiciones)) if condiciones else ""
        with get_connection() as conn:
            filas = conn.execute(
                "SELECT id, fecha, sitio_nombre, gas, valor, unidad, condicion_luz, latitud, longitud, "
                f"usuario_nombre, estado, creado FROM mediciones_chat {donde} ORDER BY fecha DESC, id DESC",
                valores,
            ).fetchall()
        columnas = ["registro", "fecha", "sitio", "gas", "valor", "unidad", "condicion_luz", "latitud",
                    "longitud", "registrada_por", "estado", "registrada_el"]
        registros = [dict(zip(columnas, (f[0], f[1].isoformat(), *f[2:11], f[11].isoformat(timespec="minutes"))))
                     for f in filas]
        if not registros:
            return {"sin_datos": True, "nota": "No hay mediciones dictadas en el chat con esos criterios."}
        salida = {"total": len(registros), "mediciones": registros[:MAX_FILAS_CONSULTA], "nota": NOTA_ORIGEN}
        if len(registros) > MAX_FILAS_CONSULTA:
            salida["nota_muestra"] = f"Se muestran las {MAX_FILAS_CONSULTA} más recientes de {len(registros)}."
        if a.get("exportar"):
            salida["exportar"] = {"titulo": "Mediciones del chat", "filas": registros}
        return salida
