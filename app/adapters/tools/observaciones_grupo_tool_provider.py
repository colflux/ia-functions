"""Consulta, desde el chat, lo que se dijo en los grupos de observación de
Telegram (por ejemplo el del sitio inventado «Paramox»): descripciones escritas
y audios transcritos, agrupados por la foto a la que se refieren."""
import re
from datetime import date, timedelta
from typing import Any

from app.domain.models import ToolSpec
from app.domain.ports.tool_provider import ToolProvider
from app.domain.services.observaciones_grupo import ObservacionesGrupo, descripcion, resumir

# Prefijo de las imágenes que el chat sabe mostrar y descargar.
PREFIJO_IMAGENES = "documentos/imagen/"
# Una pregunta general por imágenes o archivos guardados mira también lo subido desde el chat.
CONSULTAR_SUBIDOS = {"herramienta": "consultar_archivos_subidos", "argumentos": {"solo_lista": True}}

MAX_TEXTOS_POR_FOTO = 15
NOTA = ("Son descripciones cualitativas que las personas de un grupo de Telegram dieron sobre fotos: "
        "lo que perciben, no mediciones. Los audios aparecen transcritos automáticamente (Whisper); los que "
        "tienen transcripcion_revisada=true los corrigió una persona, los demás pueden tener errores. Cita los textos tal cual, sin inventar.")


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
                "en audio sobre ellas. Devuelve, por foto, cuántas descripciones y cuántas personas hubo, y "
                "los textos; las fotos se pueden ver y descargar desde el chat. Filtra por sitio y fecha. "
                "Con exportar=true entrega todos los mensajes para el Excel."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "sitio": {"type": "string", "description": "Nombre del sitio del grupo, por ejemplo Paramox"},
                    "fecha": {"type": "string", "description": "AAAA-MM-DD, AAAA-MM, «hoy» o «ayer»"},
                    "foto": {"type": "integer", "description": "Número de la foto (mensaje) si se pregunta por una"},
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
        filas = self._servicio.consultar(str(arguments.get("sitio") or "").strip(), desde, hasta,
                                         int(foto) if foto not in (None, "") else None)
        if not filas:
            grupos = [g["sitio"] for g in self._servicio.grupos()]
            vacio = {"sin_datos": True,
                     "nota": "No hay mensajes de grupos de observación con esos criterios. "
                             + (f"Grupos registrados: {', '.join(grupos)}." if grupos else "No hay grupos registrados.")}
            if not any(arguments.get(k) for k in ("sitio", "fecha", "foto")):
                vacio["consultar_tambien"] = CONSULTAR_SUBIDOS
            return vacio
        datos = resumir(filas)
        sources = []
        for foto_ in datos["por_foto"]:
            archivo = foto_.pop("archivo", None)
            if archivo and archivo.startswith(PREFIJO_IMAGENES):
                pie = f": {foto_['pie_de_foto']}" if foto_["pie_de_foto"] else ""
                sources.append({"source": archivo, "score": 1.0,
                                "content": f"Foto {foto_['foto']} del grupo {foto_['sitio']}{pie} "
                                           f"({foto_['descripciones']} descripciones)"})
                foto_["imagen"] = "se muestra y se puede descargar debajo de la respuesta"
            if len(foto_["textos"]) > MAX_TEXTOS_POR_FOTO:
                foto_["nota_muestra"] = f"Se muestran {MAX_TEXTOS_POR_FOTO} de {len(foto_['textos'])} descripciones."
                foto_["textos"] = foto_["textos"][:MAX_TEXTOS_POR_FOTO]
        datos["nota"] = NOTA
        if sources:
            datos["sources"] = sources
        if not any(arguments.get(k) for k in ("sitio", "fecha", "foto")):
            # Pregunta general («¿qué imágenes tienes?»): también lo subido desde el chat.
            datos["consultar_tambien"] = CONSULTAR_SUBIDOS
        if arguments.get("exportar"):
            datos["exportar"] = {"titulo": "Observaciones del grupo", "filas": [
                {"registro": f["registro"], "sitio": f["sitio"], "fecha": f["fecha"], "autor": f["autor"],
                 "tipo": f["tipo"], "foto": f["foto"], "descripcion": descripcion(f),
                 "transcripcion_whisper": f["transcripcion"] or "",
                 "transcripcion_corregida": f["transcripcion_corregida"] or "",
                 "estado_transcripcion": f["estado_transcripcion"] or ""}
                for f in filas if f["tipo"] in ("foto", "texto", "audio")]}
        return datos
