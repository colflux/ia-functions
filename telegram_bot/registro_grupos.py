"""Registro de los grupos de observación (por ejemplo el del sitio «Paramox»).

En un grupo registrado con /observacion <sitio> el bot guarda en silencio todo
lo que se dice: textos, fotos y audios (que transcribe con Whisper). Solo
responde a los comandos de los administradores del grupo y, como en cualquier
grupo, cuando lo mencionan.
"""
import logging
import re
import threading
import time
from datetime import datetime, timezone

from telegram_bot.grupos import Grupos
from telegram_bot.telegram import Telegram
from telegram_bot.transcriptor import Transcriptor

logger = logging.getLogger("telegram_bot.grupos")

CACHE_ESTADO_S = 60
DEPURAR_CADA_S = 24 * 3600
MAX_DESCARGA = 20 * 1024 * 1024

REGISTRADO = ("Listo: este grupo queda como grupo de observación del sitio «{sitio}». Desde ahora guardo "
              "todo lo que se diga aquí (textos, fotos y audios). Los audios se transcriben y se borran a "
              "los {dias} días; la transcripción se conserva. Para dejar de guardar: /pausar")
RESPONDE_A_AUDIO = "Usa este comando respondiendo a una nota de voz o audio del grupo."
NO_ES_AUDIO = "Ese mensaje no es un audio guardado."
USO_CORREGIR = "Responde al audio con: /corregir <lo que realmente dijo>"
CORREGIDO = "Corrección guardada. La transcripción de Whisper se conserva aparte para comparar."
COMANDOS = ("/observacion", "/pausar", "/resumen", "/transcripcion", "/corregir", "/datos", "/ia")
RESPONDE_A_FOTO = "Usa este comando respondiendo a una foto del grupo."
NO_ES_FOTO = "Ese mensaje no es una foto guardada."
USO_DATOS = ("Responde a la foto con: /datos <sitio> <AAAA-MM-DD> [dia|noche]\n"
             "Por ejemplo: /datos Laguna Seca 2021-10-05 dia")
DATOS_OK = "Listo: esta foto queda asociada a {sitio}, {fecha}{luz}. Con eso se comparará con las mediciones de ese día."
USO = "Escribe el nombre del sitio, por ejemplo: /observacion Paramox"
SOLO_ADMIN = "Solo los administradores del grupo pueden usar ese comando."
PAUSADO = "Listo: dejo de guardar los mensajes de este grupo. Lo ya guardado se conserva."
NO_REGISTRADO = "Este grupo no está registrado como grupo de observación. Usa /observacion <sitio>."


def tipo_y_archivo(mensaje: dict) -> tuple[str, dict | None, str]:
    """(tipo, {file_id, tamano, mime, nombre} o None, texto) de un mensaje de Telegram."""
    texto = (mensaje.get("text") or mensaje.get("caption") or "").strip()
    if mensaje.get("photo"):
        mayor = mensaje["photo"][-1]
        return "foto", {"file_id": mayor["file_id"], "tamano": mayor.get("file_size", 0),
                        "mime": "image/jpeg", "nombre": "foto.jpg"}, texto
    documento = mensaje.get("document") or {}
    if (documento.get("mime_type") or "").startswith("image/"):
        return "foto", {"file_id": documento["file_id"], "tamano": documento.get("file_size", 0),
                        "mime": documento["mime_type"], "nombre": documento.get("file_name") or "imagen"}, texto
    for clave, mime in (("voice", "audio/ogg"), ("audio", "audio/mpeg"), ("video_note", "video/mp4")):
        if mensaje.get(clave):
            a = mensaje[clave]
            return "audio", {"file_id": a["file_id"], "tamano": a.get("file_size", 0),
                             "mime": a.get("mime_type") or mime, "nombre": a.get("file_name") or clave,
                             "duracion": a.get("duration")}, texto
    if mensaje.get("location"):
        # Ubicación compartida (📎 → Ubicación). Un lugar con nombre trae su título.
        return "ubicacion", None, ((mensaje.get("venue") or {}).get("title") or "").strip()
    if mensaje.get("text"):
        return "texto", None, texto
    return "otro", None, texto


def ubicacion(mensaje: dict) -> dict:
    lugar = mensaje.get("location") or {}
    if "latitude" not in lugar:
        return {}
    return {"latitud": lugar["latitude"], "longitud": lugar["longitude"],
            "precision_m": lugar.get("horizontal_accuracy"), "origen_ubicacion": "compartida"}


def autor(mensaje: dict) -> tuple[int | None, str]:
    persona = mensaje.get("from") or {}
    nombre = " ".join(p for p in (persona.get("first_name"), persona.get("last_name")) if p)
    if persona.get("username"):
        nombre = f"{nombre} (@{persona['username']})".strip()
    return persona.get("id"), nombre


def leer_datos(argumento: str) -> tuple[str, str, str | None] | None:
    """«Laguna Seca 2021-10-05 dia» → ("Laguna Seca", "2021-10-05", "dia")."""
    coincide = re.fullmatch(r"\s*(.+?)\s+(\d{4}-\d{2}-\d{2})(?:\s+(d[ií]a|noche))?\s*", argumento or "", re.IGNORECASE)
    if not coincide:
        return None
    luz = coincide.group(3)
    return coincide.group(1), coincide.group(2), ("dia" if luz and luz.lower().startswith("d") else luz and "noche")


def _atributos(atributos: dict | None) -> str:
    if not atributos:
        return "nada claro"
    return "; ".join(f"{k.replace('_', ' ')}: {', '.join(v) if isinstance(v, list) else str(v).replace('_', ' ')}"
                     for k, v in atributos.items() if k != "certeza")


def texto_ia(datos: dict) -> str:
    texto = f"La IA ve:\n«{datos.get('ia_descripcion') or 'sin observación'}»\n\nAtributos: {_atributos(datos.get('ia_atributos'))}"
    if datos.get("dato_fecha"):
        luz = f", de {datos['dato_luz']}" if datos.get("dato_luz") else ""
        texto += f"\n\nDatos del día: {datos.get('dato_sitio')}, {datos['dato_fecha']}{luz}"
    else:
        texto += "\n\nSin datos del día. Responde a la foto con /datos <sitio> <AAAA-MM-DD> [dia|noche]"
    return texto


def texto_transcripcion(datos: dict) -> str:
    estado = datos.get("estado_transcripcion")
    if estado == "pendiente":
        return "Este audio todavía se está transcribiendo. Intenta de nuevo en un momento."
    if estado == "fallida":
        return "No se pudo transcribir este audio."
    if estado == "vacia":
        return "Whisper no entendió nada en este audio."
    texto = f"Whisper entendió:\n«{datos.get('transcripcion') or ''}»"
    if datos.get("transcripcion_corregida"):
        texto += f"\n\nCorregido por {datos.get('corregida_por') or 'alguien'}:\n«{datos['transcripcion_corregida']}»"
    else:
        texto += "\n\nSi no es correcto, responde al audio con /corregir <lo que realmente dijo>"
    if datos.get("atributos"):
        texto += f"\n\nAtributos que menciona: {_atributos(datos['atributos'])}"
    return texto


class RegistroGrupos:
    def __init__(self, telegram: Telegram, grupos: Grupos, transcriptor: Transcriptor | None) -> None:
        self._tg = telegram
        self._grupos = grupos
        self._transcriptor = transcriptor
        self._estado: dict[int, tuple[bool, float]] = {}
        self._admins: dict[tuple[int, int], tuple[bool, float]] = {}

    def iniciar(self) -> None:
        threading.Thread(target=self._mantenimiento, name="grupos", daemon=True).start()

    def _mantenimiento(self) -> None:
        # Al arrancar: audios que quedaron sin transcribir. Cada día: borrar los
        # audios vencidos (GRUPOS_DIAS_AUDIO en la api, 5 por defecto).
        while True:
            listo = True
            if self._transcriptor:
                try:
                    for audio in self._grupos.audios_pendientes():
                        self._transcriptor.encolar(audio["id"], lambda i=audio["id"]: self._grupos.archivo(i))
                except Exception:
                    logger.warning("No se pudieron leer los audios pendientes; reintento en 1 min")
                    listo = False
            try:
                self._grupos.depurar_audios()
            except Exception:
                logger.warning("No se pudieron depurar los audios; reintento en 1 min")
                listo = False
            # Si la api todavía no arrancaba, se reintenta pronto en vez de esperar un día.
            time.sleep(DEPURAR_CADA_S if listo else 60)

    def activo(self, chat_id: int) -> bool:
        guardado = self._estado.get(chat_id)
        if guardado and guardado[1] > time.monotonic():
            return guardado[0]
        activo = bool(self._grupos.estado(chat_id).get("activo"))
        self._estado[chat_id] = (activo, time.monotonic() + CACHE_ESTADO_S)
        return activo

    def comando(self, mensaje: dict, orden: str, argumento: str) -> bool:
        """Atiende los comandos de administración. Devuelve True si era uno de ellos."""
        if orden not in COMANDOS:
            return False
        chat = mensaje["chat"]["id"]
        persona, nombre = autor(mensaje)
        if not persona or not self._tg.es_admin(chat, persona):
            self._tg.enviar(chat, SOLO_ADMIN, mensaje["message_id"])
            return True
        if orden == "/observacion":
            sitio = argumento.strip()
            if not sitio:
                self._tg.enviar(chat, USO, mensaje["message_id"])
                return True
            datos = self._grupos.registrar(chat, mensaje["chat"].get("title") or "", sitio, nombre)
            self._estado.pop(chat, None)
            self._tg.enviar(chat, REGISTRADO.format(sitio=datos["sitio"], dias=datos.get("dias_audio", 5)))
        elif orden == "/pausar":
            self._grupos.pausar(chat)
            self._estado.pop(chat, None)
            self._tg.enviar(chat, PAUSADO)
        elif orden in ("/transcripcion", "/corregir"):
            self._revisar(mensaje, orden, argumento, nombre)
        elif orden in ("/datos", "/ia"):
            self._foto(mensaje, orden, argumento)
        else:
            if not self.activo(chat):
                self._tg.enviar(chat, NO_REGISTRADO)
                return True
            r = self._grupos.resumen(chat)
            lineas = [f"Mensajes guardados: {r['mensajes']} · fotos: {r['fotos']} · "
                      f"personas que describieron: {r['personas_que_describieron']}"]
            for f in r["por_foto"]:
                pendientes = f" ({f['audios_por_transcribir']} audios por transcribir)" if f["audios_por_transcribir"] else ""
                etiqueta = f"Foto {f['foto']}" if isinstance(f["foto"], int) else "Antes de la primera foto"
                lineas.append(f"{etiqueta}: {f['descripciones']} descripciones de {f['personas']} personas{pendientes}")
            self._tg.enviar(chat, "\n".join(lineas))
        return True

    def _foto(self, mensaje: dict, orden: str, argumento: str) -> None:
        """Respondiendo a una foto: /datos guarda el sitio y día reales en que se
        tomó; /ia muestra lo que la IA ve en ella."""
        chat = mensaje["chat"]["id"]
        foto = (mensaje.get("reply_to_message") or {}).get("message_id")
        if not foto:
            self._tg.enviar(chat, RESPONDE_A_FOTO, mensaje["message_id"])
            return
        try:
            datos = self._grupos.mensaje(chat, foto)
        except Exception:
            datos = None
        if not datos or datos["tipo"] != "foto":
            self._tg.enviar(chat, NO_ES_FOTO, mensaje["message_id"])
            return
        if orden == "/ia":
            self._tg.enviar(chat, texto_ia(datos), foto)
            return
        partes = leer_datos(argumento)
        if not partes:
            self._tg.enviar(chat, USO_DATOS, mensaje["message_id"])
            return
        sitio, fecha, luz = partes
        self._grupos.datos_foto(chat, foto, sitio, fecha, luz)
        self._tg.enviar(chat, DATOS_OK.format(sitio=sitio, fecha=fecha, luz=f", de {luz}" if luz else ""), foto)

    def _revisar(self, mensaje: dict, orden: str, argumento: str, nombre: str) -> None:
        """/transcripcion y /corregir, respondiendo a un audio: muestra lo que
        entendió Whisper o guarda lo que realmente se dijo."""
        chat = mensaje["chat"]["id"]
        audio = (mensaje.get("reply_to_message") or {}).get("message_id")
        if not audio:
            self._tg.enviar(chat, RESPONDE_A_AUDIO, mensaje["message_id"])
            return
        try:
            datos = self._grupos.mensaje(chat, audio)
        except Exception:
            datos = None
        if not datos or datos["tipo"] != "audio":
            self._tg.enviar(chat, NO_ES_AUDIO, mensaje["message_id"])
            return
        if orden == "/corregir":
            if not argumento.strip():
                self._tg.enviar(chat, USO_CORREGIR, mensaje["message_id"])
                return
            self._grupos.corregir(chat, audio, argumento.strip(), nombre)
            self._tg.enviar(chat, CORREGIDO, audio)
            return
        self._tg.enviar(chat, texto_transcripcion(datos), audio)

    def _es_admin(self, chat_id: int, persona: int | None) -> bool:
        if not persona:
            return False
        guardado = self._admins.get((chat_id, persona))
        if guardado and guardado[1] > time.monotonic():
            return guardado[0]
        try:
            admin = self._tg.es_admin(chat_id, persona)
        except Exception:
            logger.exception("No se pudo saber si %s es administrador", persona)
            return False
        self._admins[(chat_id, persona)] = (admin, time.monotonic() + CACHE_ESTADO_S * 10)
        return admin

    def guardar(self, mensaje: dict, al_bot: bool = False) -> None:
        """Se guardan las fotos que envía el equipo (administradores del grupo),
        los textos y audios de las personas y las ubicaciones que compartan. No
        se guardan stickers, GIF, videos ni documentos, ni las preguntas al bot
        (`al_bot`), ni fotos de participantes."""
        tipo, archivo, texto = tipo_y_archivo(mensaje)
        persona, nombre = autor(mensaje)
        if tipo == "otro" or al_bot:
            return
        if tipo == "foto" and not self._es_admin(mensaje["chat"]["id"], persona):
            return
        datos = {
            "chat_id": mensaje["chat"]["id"],
            "message_id": mensaje["message_id"],
            "fecha": datetime.fromtimestamp(mensaje["date"], tz=timezone.utc).isoformat(),
            "tipo": tipo,
            "autor_id": persona,
            "autor_nombre": nombre,
            "texto": texto,
            "respuesta_a": (mensaje.get("reply_to_message") or {}).get("message_id"),
            "duracion_s": (archivo or {}).get("duracion"),
            "tipo_archivo": (archivo or {}).get("mime"),
            **ubicacion(mensaje),
        }
        contenido = None
        if archivo:
            if archivo["tamano"] and archivo["tamano"] > MAX_DESCARGA:
                logger.warning("Archivo de %s bytes en el mensaje %s: Telegram no deja descargarlo",
                               archivo["tamano"], mensaje["message_id"])
            else:
                try:
                    contenido = self._tg.descargar(archivo["file_id"])
                except Exception:
                    logger.exception("No se pudo descargar el archivo del mensaje %s", mensaje["message_id"])
        adjunto = (archivo["nombre"], contenido, archivo["mime"]) if contenido else None
        resultado = self._grupos.guardar(datos, adjunto)
        if not resultado.get("guardado"):
            return
        if tipo == "audio" and contenido and self._transcriptor:
            self._transcriptor.encolar(resultado["id"], lambda c=contenido: c)
