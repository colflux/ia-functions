"""Rutas internas de los grupos de observación de Telegram.

Solo las usa el bot (servicio `telegram` del mismo docker compose). Exigen la
cabecera X-Grupos-Token igual a GRUPOS_TOKEN; si GRUPOS_TOKEN está vacío las
rutas responden 503 y nada se guarda. Nginx no las publica: /ia/ solo lleva a
las rutas del chat, pero aun así el token es obligatorio.
"""
import hashlib
import hmac
import json
import re
import unicodedata
import logging
from datetime import date, datetime

from fastapi import APIRouter, File, Form, Header, HTTPException, Response, UploadFile
from pydantic import BaseModel

from functools import lru_cache

from app.adapters.vision.gemini_observador import GeminiObservador
from app.bootstrap.container import get_almacen, get_llm_provider, get_observaciones_grupo
from app.config import settings
from app.domain.services.filtro_descripciones import FiltroDescripciones, sin_contenido
from app.domain.services.observaciones_grupo import MensajeGrupo, resumir

logger = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/grupos")

MAX_ARCHIVO_MB = 20  # lo máximo que un bot puede descargar de Telegram
EXTENSIONES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "audio/ogg": "ogg",
               "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/wav": "wav"}


@lru_cache
def _filtro() -> FiltroDescripciones:
    return FiltroDescripciones(get_llm_provider())


@lru_cache
def _observador() -> GeminiObservador | None:
    return GeminiObservador(settings.gemini_api_key, settings.image_model) if settings.gemini_api_key else None


def observar_foto(contenido: bytes, tipo: str) -> tuple[bool, dict | None]:
    """(sirve para COLFLUX, lo que ve la IA). Una foto que no muestra un
    ecosistema, plantas, agua, suelo o trabajo de campo (una mascota, comida, un
    meme) no se guarda. Si no se puede revisar, se guarda sin observación."""
    observador = _observador()
    if observador is None:
        return True, None
    try:
        observacion = observador.observar(contenido, tipo)
    except Exception:
        logger.warning("GRUPO no se pudo revisar la foto; se guarda", exc_info=True)
        return True, None
    if observacion and observacion["relacionado"] is False:
        logger.info("GRUPO foto descartada: %s", observacion.get("descripcion", "")[:120])
        return False, observacion
    logger.info("GRUPO foto observada: %s", (observacion or {}).get("atributos"))
    return True, observacion


def gps_de_foto(contenido: bytes) -> tuple[float, float] | None:
    """Coordenadas EXIF de la foto. Telegram las quita de las fotos normales;
    solo quedan si se envía como archivo."""
    try:
        import io

        from PIL import Image
        gps = Image.open(io.BytesIO(contenido)).getexif().get_ifd(0x8825)
        if not gps or 2 not in gps or 4 not in gps:
            return None
        def grados(v, ref):
            valor = float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
            return -valor if ref in ("S", "W") else valor
        return round(grados(gps[2], gps.get(1)), 6), round(grados(gps[4], gps.get(3)), 6)
    except Exception:
        return None


def _foto_relacionada(contenido: bytes, tipo: str) -> bool:
    return observar_foto(contenido, tipo)[0]


def _descartado(motivo: str) -> dict:
    return {"guardado": False, "motivo": motivo}


def _verificar(token: str | None) -> None:
    if not settings.grupos_token:
        raise HTTPException(503, "Los grupos de observación no están activados (falta GRUPOS_TOKEN).")
    if not token or not hmac.compare_digest(token, settings.grupos_token):
        raise HTTPException(401, "Token inválido.")


class RegistroGrupo(BaseModel):
    chat_id: int
    nombre: str = ""
    sitio: str
    registrado_por: str = ""


class Correccion(BaseModel):
    texto: str
    por: str = ""


class Transcripcion(BaseModel):
    id: int
    texto: str | None = None
    estado: str  # lista | vacia | fallida


@router.post("/registrar")
def registrar(datos: RegistroGrupo, x_grupos_token: str | None = Header(default=None)) -> dict:
    _verificar(x_grupos_token)
    sitio = datos.sitio.strip()
    if not sitio:
        raise HTTPException(422, "Falta el nombre del sitio.")
    get_observaciones_grupo().registrar_grupo(datos.chat_id, datos.nombre.strip(), sitio[:120],
                                              datos.registrado_por.strip())
    return {"ok": True, "sitio": sitio[:120], "dias_audio": settings.grupos_dias_audio}


@router.post("/{chat_id}/pausar")
def pausar(chat_id: int, x_grupos_token: str | None = Header(default=None)) -> dict:
    _verificar(x_grupos_token)
    return {"ok": get_observaciones_grupo().pausar_grupo(chat_id)}


@router.get("")
def listar(x_grupos_token: str | None = Header(default=None)) -> list[dict]:
    _verificar(x_grupos_token)
    return get_observaciones_grupo().grupos()


@router.get("/{chat_id}/estado")
def estado(chat_id: int, x_grupos_token: str | None = Header(default=None)) -> dict:
    _verificar(x_grupos_token)
    grupo = get_observaciones_grupo().grupo_activo(chat_id)
    return {"activo": grupo is not None, **(grupo or {})}


@router.post("/mensajes")
def guardar_mensaje(
    datos: str = Form(...),
    archivo: UploadFile | None = File(default=None),
    x_grupos_token: str | None = Header(default=None),
) -> dict:
    """`datos` es el JSON del mensaje (MensajeGrupo); `archivo`, la foto o el
    audio si lo hay. Solo se guardan fotos y descripciones de ellas: un texto
    que no describe la foto o el sitio no se guarda; un audio se guarda, y si al
    transcribirlo resulta no ser una descripción, se borra (ver /transcripcion)."""
    _verificar(x_grupos_token)
    try:
        crudo = json.loads(datos)
        crudo["fecha"] = datetime.fromisoformat(crudo["fecha"])
        mensaje = MensajeGrupo(**crudo)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, f"Mensaje inválido: {exc}")

    servicio = get_observaciones_grupo()
    grupo = servicio.grupo_activo(mensaje.chat_id)
    if not grupo:
        return _descartado("grupo no registrado o pausado")
    if mensaje.tipo not in ("foto", "texto", "audio", "ubicacion"):
        return _descartado("no es foto, texto, audio ni ubicación")
    if mensaje.tipo in ("texto", "audio"):
        if servicio.foto_para(mensaje) is None:
            return _descartado("todavía no hay una foto que describir")
        if mensaje.tipo == "texto":
            if sin_contenido(mensaje.texto):
                return _descartado("sin contenido (emojis, risas)")
            # No se descarta nada por no parecer descripción: se marca, para
            # revisarlo después (es_descripcion = false).
            mensaje.es_descripcion, mensaje.atributos = _filtro().analizar(mensaje.texto)

    clave = None
    if archivo is not None:
        contenido = archivo.file.read(MAX_ARCHIVO_MB * 1024 * 1024 + 1)
        if len(contenido) > MAX_ARCHIVO_MB * 1024 * 1024:
            raise HTTPException(413, f"El archivo pasa de {MAX_ARCHIVO_MB} MB.")
        almacen = get_almacen()
        if almacen is None:
            logger.warning("GRUPO sin bucket configurado: el archivo del mensaje %s no se guarda", mensaje.message_id)
        else:
            tipo = archivo.content_type or mensaje.tipo_archivo or "application/octet-stream"
            if mensaje.tipo == "foto":
                sirve, observacion = observar_foto(contenido, tipo)
                if not sirve:
                    return _descartado("la foto no tiene que ver con COLFLUX")
                if observacion:
                    mensaje.ia_descripcion, mensaje.ia_atributos = observacion["descripcion"], observacion["atributos"]
                gps = gps_de_foto(contenido)
                if gps and mensaje.latitud is None:
                    mensaje.latitud, mensaje.longitud = gps
                    mensaje.origen_ubicacion = "GPS de la foto"
            extension = EXTENSIONES.get(tipo, (archivo.filename or "").rsplit(".", 1)[-1][:5] or "bin")
            clave = _clave(mensaje, grupo["sitio"], extension)
            almacen.subir(clave, contenido, tipo, {"chat": str(mensaje.chat_id), "mensaje": str(mensaje.message_id)})
            mensaje.tipo_archivo = tipo

    resultado = servicio.guardar(mensaje, clave)
    if resultado is None:
        return {"guardado": False, "motivo": "grupo no registrado o pausado"}
    return {"guardado": not resultado["duplicado"], **resultado}


@router.get("/{chat_id}/mensajes/{message_id}")
def mensaje(chat_id: int, message_id: int, x_grupos_token: str | None = Header(default=None)) -> dict:
    """Lo guardado de un mensaje: sirve para revisar la transcripción de un audio."""
    _verificar(x_grupos_token)
    datos = get_observaciones_grupo().mensaje(chat_id, message_id)
    if datos is None:
        raise HTTPException(404, "Ese mensaje no está guardado.")
    return datos


@router.post("/{chat_id}/mensajes/{message_id}/correccion")
def corregir(chat_id: int, message_id: int, datos: Correccion,
             x_grupos_token: str | None = Header(default=None)) -> dict:
    _verificar(x_grupos_token)
    if not datos.texto.strip():
        raise HTTPException(422, "Falta el texto corregido.")
    servicio = get_observaciones_grupo()
    if not servicio.corregir(chat_id, message_id, datos.texto, datos.por.strip()):
        raise HTTPException(404, "Ese mensaje no es un audio guardado.")
    # Los atributos se recalculan con el texto corregido.
    guardado = servicio.mensaje(chat_id, message_id)
    if guardado:
        es, atributos = _filtro().analizar(datos.texto)
        servicio.guardar_atributos(guardado["id"], atributos, es)
    return {"ok": True}


class DatosFoto(BaseModel):
    sitio: str
    fecha: date
    luz: str | None = None


@router.post("/{chat_id}/fotos/{message_id}/datos")
def datos_foto(chat_id: int, message_id: int, datos: DatosFoto,
               x_grupos_token: str | None = Header(default=None)) -> dict:
    """Sitio y día reales de la foto (y día o noche), para comparar lo
    descrito con las mediciones de ese día."""
    _verificar(x_grupos_token)
    luz = (datos.luz or "").lower() or None
    if luz not in (None, "dia", "noche"):
        raise HTTPException(422, "luz debe ser dia o noche.")
    if not datos.sitio.strip():
        raise HTTPException(422, "Falta el sitio.")
    if not get_observaciones_grupo().datos_de_foto(chat_id, message_id, datos.sitio.strip()[:120], datos.fecha, luz):
        raise HTTPException(404, "Ese mensaje no es una foto guardada.")
    return {"ok": True}


def _clave(mensaje: MensajeGrupo, sitio: str, extension: str) -> str:
    """Las fotos van con las imágenes subidas desde el chat (documentos/imagen/),
    con el mismo formato de nombre: así el chat las muestra y ofrece descargarlas
    con los mismos permisos (verlas cualquiera, descargar el original desde nivel
    reportador). Los audios quedan aparte en grupos/, porque se borran a los días."""
    dia = mensaje.fecha.date().isoformat()
    if mensaje.tipo == "foto":
        sello = hashlib.md5(f"{mensaje.chat_id}:{mensaje.message_id}".encode()).hexdigest()[:8]
        nombre = unicodedata.normalize("NFKD", sitio).encode("ascii", "ignore").decode().lower()
        nombre = re.sub(r"[^a-z0-9]+", "-", nombre).strip("-") or "grupo"
        return f"documentos/imagen/{dia}-{sello}-{nombre}-foto-{mensaje.message_id}.{extension}"
    return f"grupos/{mensaje.chat_id}/{dia}/{mensaje.message_id}.{extension}"


@router.get("/audios/pendientes")
def audios_pendientes(x_grupos_token: str | None = Header(default=None)) -> list[dict]:
    """Audios guardados que todavía no tienen transcripción (por ejemplo,
    porque el bot se reinició antes de transcribirlos)."""
    _verificar(x_grupos_token)
    return get_observaciones_grupo().audios_pendientes()


@router.get("/archivos/{id_}")
def archivo(id_: int, x_grupos_token: str | None = Header(default=None)) -> Response:
    _verificar(x_grupos_token)
    datos = get_observaciones_grupo().archivo_de(id_)
    almacen = get_almacen()
    contenido = almacen.leer(datos[0]) if datos and almacen else None
    if contenido is None:
        raise HTTPException(404, "El archivo ya no está.")
    return Response(contenido, media_type=datos[1])


@router.post("/transcripcion")
def transcripcion(datos: Transcripcion, x_grupos_token: str | None = Header(default=None)) -> dict:
    _verificar(x_grupos_token)
    if datos.estado not in ("lista", "vacia", "fallida"):
        raise HTTPException(422, "estado debe ser lista, vacia o fallida.")
    servicio = get_observaciones_grupo()
    texto = (datos.texto or "").strip() or None
    ok = servicio.guardar_transcripcion(datos.id, texto, datos.estado)
    # Ningún audio se borra por su contenido: se marca si describe la foto o
    # no (es_descripcion), para revisarlo después.
    if ok and datos.estado == "lista":
        es, atributos = _filtro().analizar(texto or "")
        servicio.guardar_atributos(datos.id, atributos, es)
    elif ok and datos.estado == "vacia":
        servicio.guardar_atributos(datos.id, None, False)
    return {"ok": ok}


@router.post("/depurar-audios")
def depurar_audios(x_grupos_token: str | None = Header(default=None)) -> dict:
    """Borra del bucket los audios de más de GRUPOS_DIAS_AUDIO días ya
    transcritos. La transcripción y el registro del mensaje se quedan."""
    _verificar(x_grupos_token)
    servicio, almacen = get_observaciones_grupo(), get_almacen()
    borrados = 0
    for audio in servicio.audios_vencidos(settings.grupos_dias_audio):
        try:
            if almacen:
                almacen.borrar(audio["archivo"])
            servicio.quitar_archivo(audio["id"])
            borrados += 1
        except Exception:
            logger.exception("GRUPO no se pudo borrar el audio %s", audio["archivo"])
    if borrados:
        logger.info("GRUPO audios de más de %s días borrados: %s", settings.grupos_dias_audio, borrados)
    return {"borrados": borrados}


@router.get("/{chat_id}/resumen")
def resumen(chat_id: int, x_grupos_token: str | None = Header(default=None)) -> dict:
    _verificar(x_grupos_token)
    filas = get_observaciones_grupo().consultar(chat_id=chat_id)
    datos = resumir(filas)
    for foto in datos["por_foto"]:
        foto.pop("textos")
    return datos
