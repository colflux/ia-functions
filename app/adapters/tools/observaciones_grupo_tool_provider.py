"""Consulta, desde el chat, lo que se dijo en los grupos de observación de
Telegram (por ejemplo el del sitio inventado «Paramox»): descripciones escritas
y audios transcritos, agrupados por la foto a la que se refieren."""
import re
from datetime import date, timedelta
from typing import Any

from app.domain.models import ToolSpec
from app.domain.ports.tool_provider import ToolProvider
from app.domain.services.atributos_sitio import CAMPOS
from app.domain.services.observaciones_grupo import ObservacionesGrupo, descripcion, resumir

# Fotos de los grupos en el bucket (el chat las muestra y permite descargarlas).
FOTO_DE_GRUPO = re.compile(r"^telegram/[^/]+/fotos/[^/]+$")
PREFIJO_IMAGENES_ANTIGUO = "documentos/imagen/"  # donde quedaban antes de separarlas por sitio

MAX_TEXTOS_POR_FOTO = 15
MAX_FOTOS_LISTA = 30
MAX_TITULO = 70
NOTA = ("Son descripciones cualitativas que las personas de un grupo de Telegram dieron sobre fotos: "
        "lo que perciben, no mediciones. Los audios aparecen transcritos automáticamente (Whisper); los que "
        "tienen transcripcion_revisada=true los corrigió una persona, los demás pueden tener errores. Cita los textos tal cual, sin inventar. "
        "'lo_que_ve_la_ia' es la descripción automática de la foto; preséntala aparte de lo que dicen las "
        "personas, sin compararlas. Son percepciones: no las conviertas en valores de flujo.")


def _fechas(valor: Any) -> tuple[date | None, date | None]:
    texto = str(valor or "").strip().lower()
    if not texto:
        return None, None
    hoy = date.today()
    if texto == "hoy":
        return hoy, hoy
    if texto == "ayer":
        return hoy - timedelta(days=1), hoy - timedelta(days=1)
    dia = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", texto)
    if dia:
        d = date.fromisoformat(texto)
        return d, d
    mes = re.fullmatch(r"(\d{4})-(\d{2})", texto)
    if mes:
        inicio = date(int(mes.group(1)), int(mes.group(2)), 1)
        fin = (inicio.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return inicio, fin
    raise ValueError("Usa la fecha como AAAA-MM-DD, AAAA-MM, «hoy» o «ayer».")


class ObservacionesGrupoToolProvider(ToolProvider):
    def __init__(self, servicio: ObservacionesGrupo) -> None:
        self._servicio = servicio

    def list_tools(self) -> list[ToolSpec]:
        return [ToolSpec(
            name="consultar_observaciones_grupo",
            description=(
                "Fotos e imágenes enviadas a los grupos de observación de Telegram (por ejemplo el del "
                "sitio Paramox) y las descripciones y percepciones que las personas escribieron o dijeron "
                "en audio sobre ellas. Sin 'foto' devuelve una lista breve (número, fecha y hora, título y "
                "cuántas descripciones tiene cada foto); con 'foto' (o ultima=true) devuelve esa foto con sus "
                "descripciones, lo que la IA ve en ella y la imagen. Filtra por sitio y fecha. Es solo lo de "
                "Telegram: los archivos subidos desde el chat se consultan con consultar_archivos_subidos. Con "
                "exportar=true entrega el Excel: cada foto y cada mensaje con "
                "su fecha, autor, ubicación y los atributos del sitio que menciona (cielo, agua, suelo, "
                "vegetación, olor...)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "sitio": {"type": "string", "description": "Nombre del sitio del grupo, por ejemplo Paramox"},
                    "fecha": {"type": "string", "description": "AAAA-MM-DD, AAAA-MM, «hoy» o «ayer»"},
                    "foto": {"type": "integer", "description": "Número de la foto si se pregunta por una concreta"},
                    "ultima": {"type": "boolean", "description": "true si se pregunta por la última foto enviada"},
                    "exportar": {"type": "boolean"},
                },
            },
        )]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name != "consultar_observaciones_grupo":
            return {"error": f"Herramienta desconocida: {name}"}
        try:
            desde, hasta = _fechas(arguments.get("fecha"))
        except ValueError as exc:
            return {"error": str(exc)}
        foto = arguments.get("foto")
        foto = int(foto) if foto not in (None, "") else None
        sitio = str(arguments.get("sitio") or "").strip()
        filas = self._servicio.consultar(sitio, desde, hasta, foto)
        if arguments.get("ultima") and foto is None and filas:
            fotos = [f for f in filas if f["tipo"] == "foto"]
            if fotos:
                foto, chat = fotos[-1]["mensaje"], fotos[-1]["chat_id"]
                filas = [f for f in filas if f["foto"] == foto and f["chat_id"] == chat]
        if foto is not None and len({f["chat_id"] for f in filas}) > 1:
            sitios_foto = sorted({f["sitio"] for f in filas})
            return {"aclarar": f"La foto {foto} existe en varios grupos ({', '.join(sitios_foto)}). "
                               "Pregunta de qué sitio es y vuelve a consultar con 'sitio'."}
        if not filas:
            grupos = [g["sitio"] for g in self._servicio.grupos()]
            return {"sin_datos": True,
                    "nota": "No hay mensajes de grupos de observación con esos criterios. "
                            + (f"Grupos registrados: {', '.join(grupos)}." if grupos else "No hay grupos registrados.")}
        datos = resumir(filas)
        salida = _detalle(datos) if foto is not None else _lista(datos)
        if arguments.get("exportar"):
            salida["exportar"] = {"titulo": "Observaciones del grupo", "filas": _filas_mensajes(filas)}
        return salida


def _titulo(foto: dict[str, Any]) -> str:
    """El pie de la foto o, si no tiene, la primera frase de lo que ve la IA."""
    texto = (foto.get("pie_de_foto") or foto.get("lo_que_ve_la_ia") or "").strip()
    texto = re.split(r"(?<=[.!?])\s", texto, maxsplit=1)[0] if texto else f"Foto {foto['foto']}"
    return texto if len(texto) <= MAX_TITULO else texto[:MAX_TITULO].rsplit(" ", 1)[0] + "…"


def _lista(datos: dict[str, Any]) -> dict[str, Any]:
    """Una línea por foto, sin textos ni imágenes: con muchas fotos la respuesta
    sería enorme. Para ver una, se pregunta por su número."""
    fotos = [f for f in datos["por_foto"] if isinstance(f["foto"], int)]
    lista = [{"sitio": f["sitio"], "foto": f["foto"], "fecha": f["foto_enviada"], "titulo": _titulo(f),
              "descripciones": f["descripciones"], "personas": f["personas"]} for f in fotos]
    salida = {"sitios": sorted({f["sitio"] for f in fotos}), "total_fotos": len(lista),
              "fotos": lista[-MAX_FOTOS_LISTA:],
              "nota": ("Responde con una lista breve: una línea por foto con su número, fecha y hora y título "
                       "(y el sitio, si hay fotos de varios sitios), "
                       "y cuántas descripciones tiene. No pegues las descripciones. Ofrece ver una foto concreta "
                       "(por su número o «la última») o descargar todo en Excel.")}
    if len(lista) > MAX_FOTOS_LISTA:
        salida["nota_muestra"] = f"Se muestran las {MAX_FOTOS_LISTA} fotos más recientes de {len(lista)}."
    return salida


def _detalle(datos: dict[str, Any]) -> dict[str, Any]:
    """Una foto: sus descripciones, lo que ve la IA y la imagen (debajo de la respuesta)."""
    sources = []
    for foto_ in datos["por_foto"]:
        archivo = foto_.pop("archivo", None)
        if archivo and (FOTO_DE_GRUPO.match(archivo) or archivo.startswith(PREFIJO_IMAGENES_ANTIGUO)):
            sources.append({"source": archivo, "score": 1.0,
                            "content": f"Foto {foto_['foto']} del grupo {foto_['sitio']}: {_titulo(foto_)} "
                                       f"({foto_['descripciones']} descripciones)"})
            foto_["imagen"] = "se muestra y se puede descargar debajo de la respuesta"
        if len(foto_["textos"]) > MAX_TEXTOS_POR_FOTO:
            foto_["nota_muestra"] = f"Se muestran {MAX_TEXTOS_POR_FOTO} de {len(foto_['textos'])} descripciones."
            foto_["textos"] = foto_["textos"][:MAX_TEXTOS_POR_FOTO]
        for t in foto_["textos"]:
            t.pop("atributos", None)  # los atributos van en el Excel, no cargan al modelo
    datos["nota"] = NOTA
    if sources:
        datos["sources"] = sources
    return datos


def _celda(valor: Any) -> str:
    if valor is None:
        return ""
    return ", ".join(valor) if isinstance(valor, list) else str(valor)


def _filas_mensajes(filas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Un renglón por foto o descripción. En las descripciones, los atributos
    que dijo la persona; en las fotos, los que ve la IA y el día de los datos."""
    salida = []
    for f in filas:
        if f["tipo"] not in ("foto", "texto", "audio", "ubicacion"):
            continue
        es_foto = f["tipo"] == "foto"
        atributos = (f.get("ia_atributos") if es_foto else f.get("atributos")) or {}
        fila = {"registro": f["registro"], "sitio": f["sitio"], "fecha": f["fecha"], "autor": f["autor"],
                "tipo": f["tipo"], "foto": f["foto"],
                "descripcion": (f.get("ia_descripcion") or "") if es_foto else descripcion(f),
                "es_descripcion": ("" if es_foto or f["tipo"] == "ubicacion" else
                                   "no" if f.get("es_descripcion") is False else "sí"),
                "fuente_atributos": "IA (foto)" if es_foto else "persona",
                "certeza": atributos.get("certeza", ""),
                "transcripcion_whisper": f["transcripcion"] or "",
                "transcripcion_corregida": f["transcripcion_corregida"] or "",
                "datos_sitio": f.get("dato_sitio") or "", "datos_fecha": f.get("dato_fecha") or "",
                "datos_luz": f.get("dato_luz") or "",
                "latitud": f.get("latitud") if f.get("latitud") is not None else "",
                "longitud": f.get("longitud") if f.get("longitud") is not None else "",
                "origen_ubicacion": f.get("origen_ubicacion") or ""}
        fila.update({campo: _celda(atributos.get(campo)) for campo in CAMPOS})
        salida.append(fila)
    return salida

