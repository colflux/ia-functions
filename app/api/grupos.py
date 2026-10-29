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
import threading
import unicodedata
import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Response, UploadFile
from pydantic import BaseModel

from functools import lru_cache

from app.adapters.vision.gemini_observador import GeminiObservador
from app.bootstrap.container import get_almacen, get_llm_provider, get_observaciones_grupo
from app.config import settings
from app.domain.services import preguntas_foto
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


MAX_DETALLES = 100  # fotos por tanda al completar las que no tienen descripción detallada
_detallando = threading.Lock()


def detallar_foto(id_: int, contenido: bytes | None = None, tipo: str | None = None) -> bool:
    """Guarda la descripción detallada que hace la IA de una foto ya guardada
    (hoja «Descripción IA» del Excel). Sin contenido, la lee del bucket."""
    observador = _observador()
    if observador is None:
        return False
    servicio = get_observaciones_grupo()
    if contenido is None:
        datos = servicio.archivo_de(id_)
        almacen = get_almacen()
        contenido = almacen.leer(datos[0]) if datos and almacen else None
        tipo = tipo or (datos[1] if datos else None)
    if not contenido:
        return False
    tipo = tipo if (tipo or "").startswith("image/") else "image/jpeg"
    try:
        detalle = observador.detallar(contenido, tipo)
    except Exception:
        logger.warning("GRUPO no se pudo describir en detalle la foto %s", id_, exc_info=True)
        return False
    if not detalle:
        return False
    servicio.guardar_detalle(id_, detalle, settings.image_model)
    logger.info("GRUPO foto %s descrita en detalle (%d caracteres)", id_, sum(len(v) for v in detalle.values()))
    return True


def detallar_pendientes(limite: int = MAX_DETALLES) -> int:
    """Las fotos que aún no tienen descripción detallada (las guardadas antes, o
    si la IA falló). Una sola tanda a la vez."""
    if not _detallando.acquire(blocking=False):
        return 0
    try:
        return sum(detallar_foto(f["id"]) for f in get_observaciones_grupo().fotos_sin_detalle(limite))
    finally:
        _detallando.release()


def _foto_relacionada(contenido: bytes, tipo: str) -> bool:
    return observar_foto(contenido, tipo)[0]


def _descartado(motivo: str, **extra) -> dict:
    return {"guardado": False, "motivo": motivo, **extra}


def _pregunta_actual(servicio, autor_id: int, en_curso: dict, preguntas: list[dict] | None = None) -> dict:
    """La pregunta que le toca a la persona sobre la foto que está describiendo."""
    preguntas = preguntas_foto.cargar() if preguntas is None else preguntas
    hechas = _hechas(servicio, autor_id, en_curso, preguntas)
    actual = preguntas_foto.siguiente(preguntas, hechas)
    de_foto = [p for p in preguntas if p["alcance"] == preguntas_foto.FOTO]
    actual["total_foto"] = len(de_foto)
    actual["respondidas"] = sum(1 for p in de_foto if p["codigo"] in hechas)
    actual["persona_pendientes"] = sum(1 for p in preguntas if p["alcance"] == preguntas_foto.PERSONA
                                       and p["codigo"] not in hechas)
    actual["persona_total"] = len(preguntas) - len(de_foto)
    return actual


def _hechas(servicio, autor_id: int, en_curso: dict, preguntas: list[dict]) -> set[str]:
    """Lo ya respondido: de la foto, lo de esa foto; de la persona, lo de cualquier foto."""
    excluir = en_curso.get("audio_en_espera")
    de_foto = servicio.respondidas(autor_id, en_curso["chat_id"], en_curso["foto"], excluir)
    de_persona = servicio.respondidas_persona(autor_id, excluir) & preguntas_foto.de_persona(preguntas)
    return (de_foto - preguntas_foto.de_persona(preguntas)) | de_persona


def _pregunta_de(guardado: dict | None) -> str | None:
    """El texto de la pregunta a la que responde un mensaje privado (None si es
    la pregunta abierta del final o un mensaje sin pregunta)."""
    if not guardado or guardado.get("canal") != "privado":
        return None
    if guardado.get("pregunta_codigo") in (None, preguntas_foto.CIERRE):
        return None
    if guardado.get("pregunta_alcance") == preguntas_foto.PERSONA:
        return None  # la caracterización no pasa por el filtro de descripciones
    return guardado.get("pregunta_texto") or None


def _es_de_persona(guardado: dict | None) -> bool:
    return bool(guardado) and guardado.get("pregunta_alcance") == preguntas_foto.PERSONA


def preguntas_foto_especiales() -> tuple[str, ...]:
    return ("no_sabe", "no_aplica", "no_responde")


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
    tareas: BackgroundTasks,
    datos: str = Form(...),
    archivo: UploadFile | None = File(default=None),
    x_grupos_token: str | None = Header(default=None),
) -> dict:
    """`datos` es el JSON del mensaje (MensajeGrupo); `archivo`, la foto o el
    audio si lo hay. Si trae `chat_origen` es una respuesta enviada en el chat
    privado con el bot sobre la foto que la persona está describiendo: responde
    la pregunta que le toca (preguntas_foto.py) y la respuesta trae la siguiente.
    Solo se guardan fotos y descripciones de ellas: un texto que no describe la
    foto o el sitio (o no responde la pregunta) no se guarda; un audio se guarda,
    y si al transcribirlo resulta no serlo, se borra (ver /transcripcion)."""
    _verificar(x_grupos_token)
    try:
        crudo = json.loads(datos)
        crudo["fecha"] = datetime.fromisoformat(crudo["fecha"])
        # El bot avisa si va a transcribir el audio: entonces la pregunta espera a saber qué dice.
        esperar_transcripcion = bool(crudo.pop("esperar_transcripcion", False))
        mensaje = MensajeGrupo(**crudo)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, f"Mensaje inválido: {exc}")

    servicio = get_observaciones_grupo()
    actual = None
    if mensaje.chat_origen is not None:
        # Descripción en el chat privado con el bot: solo textos y audios, sobre
        # la foto que la persona está describiendo y respondiendo la pregunta que le toca.
        mensaje.canal = "privado"
        if mensaje.tipo not in ("texto", "audio"):
            return _descartado("en privado solo se guardan textos y audios")
        en_curso = servicio.descripcion_en_curso(mensaje.autor_id) if mensaje.autor_id else None
        if not en_curso:
            return _descartado("no hay una descripción en curso")
        mensaje.chat_id, mensaje.foto_message_id = en_curso["chat_id"], en_curso["foto"]
        actual = _pregunta_actual(servicio, mensaje.autor_id, en_curso)
        mensaje.pregunta_codigo, mensaje.pregunta_texto = actual["codigo"], actual["pregunta"]
        mensaje.pregunta_alcance = actual["alcance"]
        mensaje.estado_respuesta = "respondida"
        if not actual["cierre"] and mensaje.tipo == "texto":
            # «No sé» y «No aplica a esta foto» (botones o escritos) responden la pregunta.
            especial = preguntas_foto.respuesta_especial(mensaje.texto)
            if especial:
                mensaje.estado_respuesta = especial
    elif mensaje.tipo not in ("foto", "texto", "audio", "ubicacion"):
        return _descartado("no es foto, texto, audio ni ubicación")
    grupo = servicio.grupo_activo(mensaje.chat_id)
    if not grupo:
        return _descartado("grupo no registrado o pausado")
    pregunta = actual["pregunta"] if actual and not actual["cierre"] else None
    de_persona = bool(actual) and actual["alcance"] == preguntas_foto.PERSONA
    if mensaje.tipo in ("texto", "audio"):
        if servicio.foto_para(mensaje) is None:
            return _descartado("todavía no hay una foto que describir")
        if de_persona and mensaje.tipo == "texto" and mensaje.estado_respuesta == "respondida":
            # Caracterización (nombre, edad...): cualquier respuesta con letras o números
            # vale; no se le pide que describa la foto ni se extraen atributos.
            if not any(c.isalnum() for c in mensaje.texto):
                return _descartado("sin contenido (emojis, risas)", pregunta=actual)
        elif mensaje.tipo == "texto" and mensaje.estado_respuesta not in preguntas_foto_especiales():
            if sin_contenido(mensaje.texto, respuesta=bool(pregunta)):
                return _descartado("sin contenido (emojis, risas)", pregunta=actual)
            mensaje.es_descripcion, mensaje.atributos = _filtro().analizar(mensaje.texto, pregunta)
            # En privado solo se guardan descripciones: «hola», saludos o temas
            # ajenos a la foto no van a la base ni al Excel. (Lo antiguo del grupo
            # se marcó con es_descripcion = false y se conserva.)
            if mensaje.chat_origen is not None and mensaje.es_descripcion is False:
                return _descartado("no es una descripción de la foto", pregunta=actual)

    clave = None
    foto_para_detallar = None
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
                foto_para_detallar = (contenido, tipo)
            extension = EXTENSIONES.get(tipo, (archivo.filename or "").rsplit(".", 1)[-1][:5] or "bin")
            clave = _clave(mensaje, grupo["sitio"], extension)
            almacen.subir(clave, contenido, tipo, {"chat": str(mensaje.chat_id), "mensaje": str(mensaje.message_id)})
            mensaje.tipo_archivo = tipo

    resultado = servicio.guardar(mensaje, clave)
    if resultado is None:
        return {"guardado": False, "motivo": "grupo no registrado o pausado"}
    if resultado.get("sin_foto"):
        if clave and get_almacen():
            get_almacen().borrar(clave)
        return _descartado("esa foto no está guardada")
    salida = {"guardado": not resultado["duplicado"], **resultado, "es_descripcion": mensaje.es_descripcion}
    if foto_para_detallar and resultado.get("id"):
        # La descripción detallada tarda unos segundos: se hace después de responder,
        # para que el botón «Describir esta foto» aparezca de una vez.
        tareas.add_task(detallar_foto, resultado["id"], *foto_para_detallar)
    if mensaje.chat_origen is not None:
        esperando = bool(esperar_transcripcion and mensaje.tipo == "audio" and resultado.get("id")
                         and not actual["cierre"])
        if esperando:
            servicio.esperar_audio(mensaje.autor_id, resultado["id"])
        en_curso = servicio.descripcion_en_curso(mensaje.autor_id)
        salida.update({"respondio": mensaje.pregunta_codigo, "estado_respuesta": mensaje.estado_respuesta,
                       "esperando_transcripcion": esperando,
                       "pregunta": _pregunta_actual(servicio, mensaje.autor_id, en_curso) if en_curso else None})
    return salida


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


class UbicacionPersona(BaseModel):
    autor_id: int
    autor_nombre: str = ""
    chat_id: int | None = None
    latitud: float
    longitud: float
    precision_m: float | None = None
    origen_ubicacion: str = "compartida"
    fecha: datetime


@router.post("/ubicaciones")
def ubicacion_persona(datos: UbicacionPersona, x_grupos_token: str | None = Header(default=None)) -> dict:
    """Ubicación que una persona compartió con el bot (en privado, con un toque)
    o una actualización de su ubicación en tiempo real."""
    _verificar(x_grupos_token)
    if not (-90 <= datos.latitud <= 90 and -180 <= datos.longitud <= 180):
        raise HTTPException(422, "Coordenadas fuera de rango.")
    actualizadas = get_observaciones_grupo().ubicacion_persona(
        datos.autor_id, datos.autor_nombre.strip(), datos.chat_id, datos.latitud, datos.longitud,
        datos.precision_m, datos.origen_ubicacion[:60], datos.fecha)
    return {"ok": True, "descripciones_actualizadas": actualizadas}


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


def carpeta_sitio(sitio: str) -> str:
    """«Laguna Verde» → «laguna-verde»: nombre de la carpeta del sitio en el bucket."""
    nombre = unicodedata.normalize("NFKD", sitio).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", nombre).strip("-") or "grupo"


def _clave(mensaje: MensajeGrupo, sitio: str, extension: str) -> str:
    """Todo lo de Telegram va aparte de lo subido desde la plataforma, por sitio:
      telegram/<sitio>/fotos/<día>-<sello>-<sitio>-foto-<mensaje>.jpg
      telegram/<sitio>/audios/<día>/<mensaje>.ogg   (se borran a los días)
    El chat muestra esas fotos y ofrece descargarlas con los mismos permisos que
    las imágenes subidas (verlas cualquiera, el original desde nivel reportador)."""
    dia = mensaje.fecha.date().isoformat()
    carpeta = carpeta_sitio(sitio)
    if mensaje.chat_origen is not None:
        # Los mensajes privados se numeran por chat: el chat va en el nombre.
        return f"telegram/{carpeta}/audios/{dia}/p{mensaje.chat_origen}-{mensaje.message_id}.{extension}"
    if mensaje.tipo == "foto":
        sello = hashlib.md5(f"{mensaje.chat_id}:{mensaje.message_id}".encode()).hexdigest()[:8]
        return f"telegram/{carpeta}/fotos/{dia}-{sello}-{carpeta}-foto-{mensaje.message_id}.{extension}"
    return f"telegram/{carpeta}/audios/{dia}/{mensaje.message_id}.{extension}"


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
    guardado = servicio.mensaje_por_id(datos.id)
    pregunta = _pregunta_de(guardado)
    ok = servicio.guardar_transcripcion(datos.id, texto, datos.estado)
    # En el grupo ningún audio se borra por su contenido: se marca si describe
    # la foto o no (es_descripcion), para revisarlo después.
    es, estado_respuesta = None, (guardado or {}).get("estado_respuesta")
    if ok and datos.estado == "lista":
        especial = preguntas_foto.respuesta_especial(texto or "") if (pregunta or _es_de_persona(guardado)) else None
        if especial:
            # Dijo «no sé» o «no aplica» en la nota de voz.
            estado_respuesta = especial
            servicio.marcar_respuesta(datos.id, especial)
        elif _es_de_persona(guardado):
            es = True  # caracterización: se guarda lo que dijo, sin filtro ni atributos
            servicio.guardar_atributos(datos.id, None, True)
        else:
            es, atributos = _filtro().analizar(texto or "", pregunta)
            servicio.guardar_atributos(datos.id, atributos, es)
    elif ok and datos.estado == "vacia":
        es = False
        servicio.guardar_atributos(datos.id, None, False)
    borrado = False
    if ok and es is False:
        # Un audio enviado en privado que no describe la foto (o en el que no se
        # entendió nada) no se conserva: se borran el registro y el archivo.
        clave = servicio.borrar_privado(datos.id)
        if clave is not None:
            borrado = True
            almacen = get_almacen()
            if clave and almacen:
                try:
                    almacen.borrar(clave)
                except Exception:
                    logger.exception("GRUPO no se pudo borrar el audio %s", clave)
    # La pregunta que esperaba este audio ya puede seguir (o repetirse, si se borró).
    servicio.audio_listo(datos.id)
    return {"ok": ok, "es_descripcion": es, "borrado": borrado,
            "pregunta_codigo": (guardado or {}).get("pregunta_codigo"), "estado_respuesta": estado_respuesta,
            "pregunta_alcance": (guardado or {}).get("pregunta_alcance")}


@router.post("/fotos/detallar")
def detallar_fotos(tareas: BackgroundTasks, x_grupos_token: str | None = Header(default=None)) -> dict:
    """Completa, en segundo plano, la descripción detallada de las fotos que no
    la tienen. El bot lo pide al arrancar y una vez al día."""
    _verificar(x_grupos_token)
    pendientes = len(get_observaciones_grupo().fotos_sin_detalle(MAX_DETALLES))
    disponible = _observador() is not None
    if pendientes and disponible:
        tareas.add_task(detallar_pendientes)
    return {"pendientes": pendientes, "ia_disponible": disponible}


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
    return {"borrados": borrados, "excel_borrados": _depurar_excel(almacen)}


DIAS_EXCEL = 7


def _depurar_excel(almacen) -> int:
    """Los Excel que genera el chat son temporales: se borran a los 7 días (si
    alguien los vuelve a pedir, se generan de nuevo). Va en la misma tarea
    diaria que los audios."""
    if almacen is None or not hasattr(almacen, "listar"):
        return 0
    limite = datetime.now(timezone.utc) - timedelta(days=DIAS_EXCEL)
    borrados = 0
    try:
        for clave, fecha in almacen.listar("descargas/"):
            if fecha < limite:
                almacen.borrar(clave)
                borrados += 1
    except Exception:
        logger.exception("GRUPO no se pudieron depurar los Excel")
    if borrados:
        logger.info("Excel de más de %s días borrados: %s", DIAS_EXCEL, borrados)
    return borrados


class InicioDescripcion(BaseModel):
    autor_id: int
    autor_nombre: str = ""
    chat_privado: int
    foto: int  # número de registro de la foto (el que lleva el botón «Describir esta foto»)


@router.post("/privado/descripcion")
def iniciar_descripcion(datos: InicioDescripcion, x_grupos_token: str | None = Header(default=None)) -> dict:
    """La persona abrió una foto desde el botón del grupo: lo que escriba o grabe
    en su chat privado con el bot describe esa foto."""
    _verificar(x_grupos_token)
    servicio = get_observaciones_grupo()
    foto = servicio.foto_por_registro(datos.foto)
    if not foto:
        raise HTTPException(404, "Esa foto no está guardada.")
    if not foto["activo"]:
        raise HTTPException(409, "Ese grupo ya no está recibiendo descripciones.")
    servicio.iniciar_descripcion(datos.autor_id, datos.autor_nombre.strip(), datos.chat_privado,
                                 foto["chat_id"], foto["message_id"])
    # Si ya había respondido parte de esta foto, sigue donde iba.
    en_curso = {"chat_id": foto["chat_id"], "foto": foto["message_id"]}
    return {"ok": True, **foto, "pregunta": _pregunta_actual(servicio, datos.autor_id, en_curso)}


@router.get("/privado/descripcion/{autor_id}")
def descripcion_en_curso(autor_id: int, x_grupos_token: str | None = Header(default=None)) -> dict:
    _verificar(x_grupos_token)
    servicio = get_observaciones_grupo()
    en_curso = servicio.descripcion_en_curso(autor_id)
    if not en_curso:
        return {"activa": False}
    return {"activa": True, **en_curso, "pregunta": _pregunta_actual(servicio, autor_id, en_curso)}


@router.delete("/privado/descripcion/{autor_id}")
def terminar_descripcion(autor_id: int, x_grupos_token: str | None = Header(default=None)) -> dict:
    _verificar(x_grupos_token)
    servicio = get_observaciones_grupo()
    cerrada = servicio.terminar_descripcion(autor_id)
    if cerrada is None:
        return {"ok": False, "mensajes": 0, "faltan": 0}
    preguntas = preguntas_foto.cargar()
    hechas = _hechas(servicio, autor_id, cerrada, preguntas)
    return {"ok": True, "mensajes": cerrada["mensajes"],
            "faltan": sum(1 for p in preguntas if p["codigo"] not in hechas)}


@router.get("/privado/{chat_origen}/mensajes/{message_id}")
def mensaje_privado(chat_origen: int, message_id: int, x_grupos_token: str | None = Header(default=None)) -> dict:
    """Un mensaje que la persona envió en privado: para revisar su transcripción."""
    _verificar(x_grupos_token)
    datos = get_observaciones_grupo().mensaje_privado(chat_origen, message_id)
    if datos is None:
        raise HTTPException(404, "Ese mensaje no está guardado.")
    return datos


@router.post("/privado/{chat_origen}/mensajes/{message_id}/correccion")
def corregir_privado(chat_origen: int, message_id: int, datos: Correccion,
                     x_grupos_token: str | None = Header(default=None)) -> dict:
    _verificar(x_grupos_token)
    if not datos.texto.strip():
        raise HTTPException(422, "Falta el texto corregido.")
    servicio = get_observaciones_grupo()
    if not servicio.corregir_privado(chat_origen, message_id, datos.texto, datos.por.strip()):
        raise HTTPException(404, "Ese mensaje no es un audio guardado.")
    guardado = servicio.mensaje_privado(chat_origen, message_id)
    if guardado and not _es_de_persona(guardado):
        es, atributos = _filtro().analizar(datos.texto, _pregunta_de(guardado))
        servicio.guardar_atributos(guardado["id"], atributos, es)
    return {"ok": True}


@router.get("/registros/{id_}")
def registro(id_: int, x_grupos_token: str | None = Header(default=None)) -> dict:
    """Un mensaje por su número de registro: el equipo revisa así la transcripción
    de un audio enviado en privado (la persona no la ve)."""
    _verificar(x_grupos_token)
    datos = get_observaciones_grupo().mensaje_por_id(id_)
    if datos is None:
        raise HTTPException(404, "Ese registro no existe.")
    return datos


@router.post("/registros/{id_}/correccion")
def corregir_registro(id_: int, datos: Correccion, x_grupos_token: str | None = Header(default=None)) -> dict:
    _verificar(x_grupos_token)
    if not datos.texto.strip():
        raise HTTPException(422, "Falta el texto corregido.")
    servicio = get_observaciones_grupo()
    if not servicio.corregir_registro(id_, datos.texto, datos.por.strip()):
        raise HTTPException(404, "Ese registro no es un audio guardado.")
    guardado = servicio.mensaje_por_id(id_)
    if guardado and not _es_de_persona(guardado):
        es, atributos = _filtro().analizar(datos.texto, _pregunta_de(guardado))
        servicio.guardar_atributos(id_, atributos, es)
    return {"ok": True}


@router.get("/{chat_id}/resumen")
def resumen(chat_id: int, x_grupos_token: str | None = Header(default=None)) -> dict:
    _verificar(x_grupos_token)
    filas = get_observaciones_grupo().consultar(chat_id=chat_id)
    datos = resumir(filas)
    for foto in datos["por_foto"]:
        foto.pop("textos")
    return datos
