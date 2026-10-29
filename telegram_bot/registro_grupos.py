"""Registro de los grupos de observación (por ejemplo el del sitio «Paramox»).

El grupo, registrado con /observacion <sitio>, es solo para que el equipo
(administradores) envíe las fotos y para reunir a quienes participan. Bajo cada
foto el bot publica el botón «Describir esta foto»: abre el chat privado con el
bot, que muestra la foto y le hace a la persona, una por una, las preguntas del
listado editable (app/data/preguntas_foto.csv en la api), con «No sé» y «No
aplica a esta foto», y al final le pregunta si observa algo más. Responde por
escrito o con notas de voz (los audios los transcribe Whisper). Así nadie ve, ni
copia, lo que respondieron los demás. La descripción sigue abierta hasta que la
persona toca «Terminar» o pasan 12 horas sin escribir; después el chat privado
vuelve a ser el asistente de siempre. Si la deja a medias, al volver a tocar el
botón sigue donde iba.
"""
import logging
import re
import unicodedata
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

REGISTRADO = ("Listo: este grupo queda como grupo de observación del sitio «{sitio}».\n\n"
              "Aquí el equipo envía las fotos. Debajo de cada una aparece el botón «✍️ Describir esta foto», "
              "que abre el chat privado con el bot: ahí cada persona responde, una por una, las preguntas del "
              "estudio sobre la foto y el lugar, por escrito o con notas de voz, sin ver lo que dicen los demás. Los audios se transcriben "
              "y se borran a los {dias} días; la "
              "transcripción se conserva. Para dejar de recibir descripciones: /pausar\n\n"
              "Recomendado: en la configuración del grupo, Permisos → desactiva «Enviar mensajes» para los "
              "miembros, para que solo el equipo publique.")
BOTON_DESCRIBIR = "✍️ Describir esta foto"
INVITA_DESCRIBIR = "Para describir esta foto toca el botón: se abre tu chat privado con el bot."
FOTO_NO_RELACIONADA = ("Esta foto no parece relacionada con COLFLUX (un ecosistema, plantas, agua, suelo o trabajo "
                       "de campo), así que no la guardé ni la abrí para describir.")
INSTRUCCIONES = ("Esta es la foto del sitio «{sitio}». Descríbela con tus palabras: lo que ves, oyes, hueles o "
                 "sientes del lugar. Puedes escribir o enviar notas de voz, en uno o varios mensajes. Lo que "
                 "describas solo lo ve el equipo del proyecto.\n\n"
                 "Si quieres que tus descripciones lleven el lugar desde donde las envías, toca «📍 Compartir mi "
                 "ubicación». Cuando termines, toca «✅ Terminar».")
INSTRUCCIONES_PREGUNTAS = (
    "Esta es la foto del sitio «{sitio}». Te haré {total} preguntas sobre este lugar y lo que ves en la foto, una "
    "por una. Responde con tus palabras, por escrito o con una nota de voz. Si una pregunta no corresponde, toca "
    "«No aplica a esta foto»; si no sabes, toca «No sé». Lo que respondas solo lo ve el equipo del proyecto.\n\n"
    "Si quieres que tus respuestas lleven el lugar desde donde las envías, toca «📍 Compartir mi ubicación».")
INSTRUCCIONES_PERSONA = (
    "Esta es la foto del sitio «{sitio}». Antes de las preguntas sobre la foto ({total} preguntas), te haré "
    "{persona} preguntas sobre ti. Estas solo se hacen esta vez, sirven para el estudio y solo las ve el equipo del "
    "proyecto; en cualquiera puedes tocar «Prefiero no responder».\n\n"
    "Responde con tus palabras, por escrito o con una nota de voz.")
SEGUIMOS = "Seguimos con la foto del sitio «{sitio}»: ya respondiste {respondidas} de {total} preguntas."
SEGUIMOS_PERSONA = "Seguimos con las preguntas sobre ti: te faltan {pendientes}. Después siguen las de la foto del sitio «{sitio}»."
YA_RESPONDIDAS = "Ya respondiste las {total} preguntas de esta foto del sitio «{sitio}»."
PREGUNTA = "Pregunta {numero} de {total} · {categoria}\n\n{pregunta}"
PREGUNTA_PERSONA = "Sobre ti · {numero} de {total}\n\n{pregunta}"
AHORA_FOTO = ("Gracias, terminamos las preguntas sobre ti. Ahora las preguntas sobre la foto y este lugar; si una no "
              "corresponde, toca «No aplica a esta foto», y si no sabes, «No sé».")
PREGUNTA_FINAL = ("Para terminar: {pregunta}\n\nSi es así, descríbelo por escrito o con una nota de voz (puedes "
                  "enviar varios mensajes). Si no, toca «✅ Terminar».")
TERMINAR = "✅ Terminar"
NO_SE = "No sé"
NO_APLICA = "No aplica a esta foto"
NO_RESPONDO = "Prefiero no responder"
TECLADO_DESCRIPCION = {"keyboard": [[{"text": "📍 Compartir mi ubicación", "request_location": True}],
                                    [{"text": TERMINAR}]],
                       "resize_keyboard": True}
TECLADO_PREGUNTA = {"keyboard": [[{"text": NO_SE}, {"text": NO_APLICA}],
                                 [{"text": "📍 Compartir mi ubicación", "request_location": True}, {"text": TERMINAR}]],
                    "resize_keyboard": True}
TECLADO_PERSONA = {"keyboard": [[{"text": NO_RESPONDO}],
                                [{"text": "📍 Compartir mi ubicación", "request_location": True}, {"text": TERMINAR}]],
                   "resize_keyboard": True}
ANOTADO = {"respondida": "✔️ Anotado.", "no_sabe": "✔️ Anotado: «No sé».", "no_aplica": "✔️ Anotado: «No aplica».",
           "no_responde": "✔️ Anotado: «Prefiero no responder»."}
# En la pregunta final, «no» o «nada más» quiere decir que terminó.
FIN = {"no", "nop", "nope", "no gracias", "nada", "nada mas", "no nada", "no nada mas", "no mas", "ninguna",
       "ninguno", "eso es todo", "es todo", "eso es", "listo", "ya", "ya esta", "ya termine", "termine", "fin",
       "no se", "no observo nada mas", "no veo nada mas", "nada mas gracias", "no eso es todo"}
FOTO_NO_DISPONIBLE = "Esa foto ya no está disponible para describir."
GUARDADO = "✔️ Guardado. Puedes agregar más o tocar «✅ Terminar»."
NO_DESCRIPCION = ("No lo guardé porque no parece una descripción de la foto. Cuéntame qué ves, oyes, hueles o "
                  "sientes en el sitio, o toca «✅ Terminar».")
NO_RESPONDE = ("No lo guardé porque no parece una respuesta sobre la foto. Responde con lo que ves, o toca «No sé» "
               "o «No aplica a esta foto».")
SIN_RESPUESTA_PERSONA = "Ese mensaje no lo guardé: no trae una respuesta. Responde, o toca «Prefiero no responder»."
SIN_RESPUESTA = "Ese mensaje no lo guardé: no trae una respuesta. Responde con lo que ves, o toca «No sé» o «No aplica a esta foto»."
AUDIO_GUARDADO = "✔️ Audio recibido. Dame un momento."
AUDIO_GUARDADO_PREGUNTA = "✔️ Audio recibido. Dame un momento y seguimos."
SIN_CONTENIDO = "Ese mensaje no lo guardé: no trae una descripción. Cuéntame qué percibes en la foto."
SOLO_TEXTO_AUDIO = "Aquí solo guardo descripciones escritas o en notas de voz."
NO_GUARDADO = "No pude guardar ese mensaje. Intenta de nuevo en un momento."
TERMINADO = ("Gracias: quedaron guardadas tus respuestas sobre esta foto ({n} mensajes). Si quieres, ahora puedes "
             "preguntarme lo que quieras sobre COLFLUX.")
TERMINADO_SIN_NADA = "Listo, no agregué nada a esta foto. Si quieres, ahora puedes preguntarme lo que quieras sobre COLFLUX."
TERMINADO_FALTAN = ("Listo, guardé lo que respondiste sobre esta foto. Te faltaron {faltan} preguntas: si quieres "
                    "completarlas, vuelve a tocar «✍️ Describir esta foto» en el grupo. Mientras tanto puedes "
                    "preguntarme lo que quieras sobre COLFLUX.")
NADA_EN_CURSO = "No estás describiendo ninguna foto. Para hacerlo, toca «✍️ Describir esta foto» en el grupo."
# La persona no ve la transcripción de sus audios: la revisa y corrige el equipo
# (/transcripcion y /corregir con el número de registro, en el chat privado con el bot).
AUDIO_LISTO = "✔️ Audio guardado. Puedes agregar más o tocar «✅ Terminar»."
AUDIO_ANOTADO = "✔️ Audio guardado."
AUDIO_NO_DESCRIPCION = ("No guardé ese audio porque no parece una descripción de la foto: cuéntame qué ves, oyes, "
                        "hueles o sientes en el sitio.")
AUDIO_NO_RESPONDE = "No guardé ese audio porque no parece una respuesta a la pregunta. Te la repito:"
NO_ENTENDI = "No logré entender ese audio, así que no lo guardé. ¿Puedes repetirlo o escribirlo?"
NO_TRANSCRITO = "No pude transcribir ese audio; quedó guardado para que el equipo lo revise."
GRACIAS_UBICACION_DESCRIPCION = ("Gracias, guardé tu ubicación: tus descripciones la llevarán. Sigue describiendo "
                                 "la foto o toca «✅ Terminar».")
GRACIAS_UBICACION_PREGUNTA = "Gracias, guardé tu ubicación: tus respuestas la llevarán. Sigamos:"
INVITA_UBICACION = ("📍 Para que tus descripciones lleven el lugar desde donde las envías, comparte tu "
                    "ubicación una vez con un toque aquí: {enlace}\n"
                    "Se usa solo en este estudio y vale para lo que describas en las 12 horas siguientes.")
PIDE_UBICACION = ("Toca el botón «📍 Compartir mi ubicación» para que tus descripciones en los grupos de "
                  "observación lleven el lugar desde donde las envías. Se usa solo en este estudio y vale "
                  "para lo que describas en las 12 horas siguientes. Si no quieres compartirla, ignora este mensaje.")
BOTON_UBICACION = {"keyboard": [[{"text": "📍 Compartir mi ubicación", "request_location": True}]],
                   "resize_keyboard": True, "one_time_keyboard": True}
GRACIAS_UBICACION = ("Gracias, guardé tu ubicación. Tus descripciones en los grupos de las próximas 12 horas "
                     "la llevarán (también las que enviaste en las últimas 12 horas sin ubicación).")
QUITAR_TECLADO = {"remove_keyboard": True}
RESPONDE_A_AUDIO = "Usa este comando respondiendo a una de tus notas de voz o audios."
SOLO_EQUIPO = "Las transcripciones de los audios las revisa el equipo del proyecto."
USO_EQUIPO = ("Para el equipo: /transcripcion <registro> muestra lo que entendió Whisper y envía el audio; "
              "/corregir <registro> <texto> guarda lo que realmente se dijo. El registro es la columna "
              "«registro» del Excel.")
NO_ES_ADMIN_REGISTRO = "Solo los administradores del grupo de ese registro pueden revisar su transcripción."
NO_ES_AUDIO = "Ese mensaje no es un audio guardado."
USO_CORREGIR = "Responde al audio con: /corregir <lo que realmente dijo>"
CORREGIDO = "Corrección guardada. La transcripción de Whisper se conserva aparte para comparar."
COMANDOS = ("/observacion", "/pausar", "/resumen", "/transcripcion", "/corregir", "/datos", "/ia", "/ubicacion")
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
            "precision_m": lugar.get("horizontal_accuracy"),
            "origen_ubicacion": "en tiempo real" if lugar.get("live_period") else "compartida en el grupo"}


def autor(mensaje: dict) -> tuple[int | None, str]:
    persona = mensaje.get("from") or {}
    nombre = " ".join(p for p in (persona.get("first_name"), persona.get("last_name")) if p)
    if persona.get("username"):
        nombre = f"{nombre} (@{persona['username']})".strip()
    return persona.get("id"), nombre


def _plano(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    return " ".join("".join(c if c.isalnum() or c == " " else " " for c in base).split())


def es_fin(texto: str) -> bool:
    """«No», «nada más», «eso es todo»: la respuesta a «¿observas algo más?» que cierra la foto."""
    return _plano(texto) in FIN


def es_de_persona(pregunta: dict | None) -> bool:
    return bool(pregunta) and pregunta.get("alcance") == "persona" and not pregunta.get("cierre")


def teclado_de(pregunta: dict | None) -> dict:
    if not pregunta or pregunta.get("cierre"):
        return TECLADO_DESCRIPCION
    return TECLADO_PERSONA if es_de_persona(pregunta) else TECLADO_PREGUNTA


def texto_pregunta(pregunta: dict) -> str:
    if pregunta.get("cierre"):
        return PREGUNTA_FINAL.format(pregunta=pregunta["pregunta"])
    numero, total = pregunta.get("numero_en") or pregunta["paso"] + 1, pregunta.get("de") or pregunta["total"]
    if es_de_persona(pregunta):
        return PREGUNTA_PERSONA.format(numero=numero, total=total, pregunta=pregunta["pregunta"])
    return PREGUNTA.format(numero=numero, total=total, categoria=pregunta.get("categoria") or "Pregunta",
                           pregunta=pregunta["pregunta"])


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
        self._usuario_bot = ""
        # Audios enviados en privado: a quién avisarle cuando se transcriban.
        self._avisar: dict[int, tuple[int, int, int | None]] = {}

    def _enlace(self, parametro: str) -> str:
        """Enlace que abre el chat privado con el bot y le envía /start <parametro>."""
        if not self._usuario_bot:
            self._usuario_bot = self._tg.yo()["username"]
        return f"https://t.me/{self._usuario_bot}?start={parametro}"

    def _enlace_ubicacion(self) -> str:
        return self._enlace("ubicacion")

    # ── ubicación ─────────────────────────────────────────────────────────
    def pedir_ubicacion(self, chat: int) -> None:
        """En el chat privado: el botón que comparte la ubicación con un toque
        (Telegram solo lo permite en chats privados)."""
        self._tg.enviar(chat, PIDE_UBICACION, teclado=BOTON_UBICACION)

    def guardar_ubicacion(self, mensaje: dict, privado: bool) -> None:
        """Ubicación compartida en el chat privado, o actualización de una
        ubicación en tiempo real (en el grupo o en privado). Queda asociada a la
        persona, no a un grupo: la toman sus descripciones de las 12 horas
        siguientes (y las de las 12 anteriores que no tenían)."""
        datos = ubicacion(mensaje)
        persona, nombre = autor(mensaje)
        if not datos or not persona:
            return
        if privado and not mensaje.get("edit_date"):
            datos["origen_ubicacion"] = "compartida en privado"
        fecha = mensaje.get("edit_date") or mensaje["date"]
        self._grupos.ubicacion_persona({
            **datos, "autor_id": persona, "autor_nombre": nombre,
            "fecha": datetime.fromtimestamp(fecha, tz=timezone.utc).isoformat(),
            "chat_id": None if privado else mensaje["chat"]["id"],
        })
        if privado and not mensaje.get("edit_date"):
            en_curso = self.en_curso(persona)
            pregunta = (en_curso or {}).get("pregunta")
            if pregunta and not pregunta.get("cierre"):
                # Va en medio de las preguntas: se agradece y se repite la que toca.
                self._tg.enviar(mensaje["chat"]["id"], GRACIAS_UBICACION_PREGUNTA)
                self._preguntar(mensaje["chat"]["id"], pregunta)
            elif en_curso:
                self._tg.enviar(mensaje["chat"]["id"], GRACIAS_UBICACION_DESCRIPCION, teclado=TECLADO_DESCRIPCION)
            else:
                self._tg.enviar(mensaje["chat"]["id"], GRACIAS_UBICACION, teclado=QUITAR_TECLADO)

    def iniciar(self) -> None:
        threading.Thread(target=self._mantenimiento, name="grupos", daemon=True).start()

    def _mantenimiento(self) -> None:
        # Al arrancar: audios que quedaron sin transcribir. Cada día: borrar los
        # audios vencidos (GRUPOS_DIAS_AUDIO en la api, 5 por defecto) y completar
        # la descripción detallada de las fotos que no la tienen.
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
            try:
                # Descripción detallada de la IA para las fotos que aún no la tienen.
                pendientes = self._grupos.detallar_fotos().get("pendientes", 0)
                if pendientes:
                    logger.info("Fotos por describir en detalle: %s", pendientes)
            except Exception:
                logger.warning("No se pudo pedir la descripción detallada de las fotos")
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
        elif orden == "/ubicacion":
            self._tg.enviar(chat, INVITA_UBICACION.format(enlace=self._enlace_ubicacion()))
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
        """En el grupo solo se guardan las fotos que envía el equipo
        (administradores). Debajo de cada una el bot publica el botón que abre el
        chat privado para describirla. Las descripciones se hacen en privado
        (guardar_privado), así que lo demás que se escriba en el grupo no se guarda."""
        tipo, archivo, texto = tipo_y_archivo(mensaje)
        persona, nombre = autor(mensaje)
        if tipo != "foto" or al_bot:
            return
        if not self._es_admin(mensaje["chat"]["id"], persona):
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
        chat = mensaje["chat"]["id"]
        if resultado.get("guardado") and resultado.get("id"):
            boton = {"inline_keyboard": [[{"text": BOTON_DESCRIBIR, "url": self._enlace(f"f{resultado['id']}")}]]}
            self._tg.enviar(chat, INVITA_DESCRIBIR, mensaje["message_id"], teclado=boton)
        elif "no tiene que ver con COLFLUX" in (resultado.get("motivo") or ""):
            self._tg.enviar(chat, FOTO_NO_RELACIONADA, mensaje["message_id"])

    # ── descripciones en el chat privado ──────────────────────────────────
    def en_curso(self, persona: int | None) -> dict | None:
        """La foto que la persona está describiendo (None si no hay ninguna o ya venció)."""
        if not persona:
            return None
        try:
            datos = self._grupos.descripcion_en_curso(persona)
        except Exception:
            logger.exception("No se pudo leer la descripción en curso de %s", persona)
            return None
        return datos if datos.get("activa") else None

    def abrir_descripcion(self, mensaje: dict, registro: int) -> None:
        """/start f<registro>: la persona tocó «Describir esta foto» en el grupo."""
        chat = mensaje["chat"]["id"]
        persona, nombre = autor(mensaje)
        datos = self._grupos.iniciar_descripcion(persona, nombre, chat, registro)
        if not datos.get("ok"):
            self._tg.enviar(chat, FOTO_NO_DISPONIBLE, teclado=QUITAR_TECLADO)
            return
        try:
            self._tg.copiar(chat, datos["chat_id"], datos["message_id"])
        except Exception:
            # Si el grupo no deja copiar su contenido, se envía desde el almacén.
            try:
                self._tg.enviar_foto(chat, self._grupos.archivo(registro))
            except Exception:
                logger.exception("No se pudo mostrar la foto %s en privado", registro)
        pregunta = datos.get("pregunta") or {}
        if not pregunta.get("total"):
            # Sin listado de preguntas: descripción libre, como antes.
            self._tg.enviar(chat, INSTRUCCIONES.format(sitio=datos["sitio"]), teclado=TECLADO_DESCRIPCION)
            return
        total = pregunta.get("total_foto", pregunta["total"])
        respondidas = pregunta.get("respondidas", 0)
        pendientes = pregunta.get("persona_pendientes", 0)
        if es_de_persona(pregunta) and pendientes == pregunta.get("persona_total"):
            intro = INSTRUCCIONES_PERSONA.format(sitio=datos["sitio"], total=total, persona=pendientes)
        elif es_de_persona(pregunta):
            intro = SEGUIMOS_PERSONA.format(pendientes=pendientes, sitio=datos["sitio"])
        elif respondidas >= total:
            intro = YA_RESPONDIDAS.format(total=total, sitio=datos["sitio"])
        elif respondidas:
            intro = SEGUIMOS.format(sitio=datos["sitio"], respondidas=respondidas, total=total)
        else:
            intro = INSTRUCCIONES_PREGUNTAS.format(sitio=datos["sitio"], total=total)
        self._tg.enviar(chat, intro)
        self._preguntar(chat, pregunta)

    def _preguntar(self, chat: int, pregunta: dict | None, antes: str = "") -> None:
        """Muestra la pregunta que toca (o la pregunta abierta del final) con sus botones."""
        if not pregunta:
            return
        texto = texto_pregunta(pregunta)
        self._tg.enviar(chat, f"{antes}\n\n{texto}" if antes else texto, teclado=teclado_de(pregunta))

    def terminar(self, mensaje: dict) -> None:
        persona, _ = autor(mensaje)
        datos = self._grupos.terminar_descripcion(persona) if persona else {"ok": False}
        if not datos.get("ok"):
            texto = NADA_EN_CURSO
        elif datos.get("faltan"):
            texto = TERMINADO_FALTAN.format(faltan=datos["faltan"])
        elif datos.get("mensajes"):
            texto = TERMINADO.format(n=datos["mensajes"])
        else:
            texto = TERMINADO_SIN_NADA
        self._tg.enviar(mensaje["chat"]["id"], texto, teclado=QUITAR_TECLADO)

    def guardar_privado(self, mensaje: dict, en_curso: dict) -> None:
        """Un texto o una nota de voz que la persona envió en privado: la
        respuesta a la pregunta que le toca sobre la foto (o, al final, lo que
        agrega). Mismas reglas que en el grupo: no se guarda lo que no trae
        contenido (emojis, risas) ni lo que no describe la foto o no responde la
        pregunta, y entonces se le repite."""
        chat = mensaje["chat"]["id"]
        tipo, archivo, texto = tipo_y_archivo(mensaje)
        actual = en_curso.get("pregunta") or {}
        if tipo not in ("texto", "audio"):
            self._tg.enviar(chat, SOLO_TEXTO_AUDIO, mensaje["message_id"], teclado=teclado_de(actual))
            return
        if tipo == "texto" and actual.get("cierre") and es_fin(texto):
            self.terminar(mensaje)
            return
        persona, nombre = autor(mensaje)
        datos = {
            "chat_id": en_curso["chat_id"],
            "message_id": mensaje["message_id"],
            "fecha": datetime.fromtimestamp(mensaje["date"], tz=timezone.utc).isoformat(),
            "tipo": tipo,
            "autor_id": persona,
            "autor_nombre": nombre,
            "texto": texto,
            "duracion_s": (archivo or {}).get("duracion"),
            "tipo_archivo": (archivo or {}).get("mime"),
            "canal": "privado",
            "chat_origen": chat,
            "foto_message_id": en_curso["foto"],
        }
        contenido = None
        if archivo:
            if archivo["tamano"] and archivo["tamano"] > MAX_DESCARGA:
                logger.warning("Audio de %s bytes: Telegram no deja descargarlo", archivo["tamano"])
            else:
                try:
                    contenido = self._tg.descargar(archivo["file_id"])
                except Exception:
                    logger.exception("No se pudo descargar el audio del mensaje %s", mensaje["message_id"])
        # Si el audio se va a transcribir, la pregunta espera a saber qué dice.
        datos["esperar_transcripcion"] = bool(tipo == "audio" and contenido and self._transcriptor)
        adjunto = (archivo["nombre"], contenido, archivo["mime"]) if contenido else None
        try:
            resultado = self._grupos.guardar(datos, adjunto)
        except Exception:
            logger.exception("No se pudo guardar la descripción privada %s", mensaje["message_id"])
            self._tg.enviar(chat, NO_GUARDADO, mensaje["message_id"])
            return
        pregunta = resultado.get("pregunta") or actual
        en_preguntas = bool(actual.get("total")) and not actual.get("cierre")
        if not resultado.get("guardado"):
            motivo = resultado.get("motivo") or ""
            if "sin contenido" in motivo:
                aviso = (SIN_RESPUESTA_PERSONA if es_de_persona(actual) else SIN_RESPUESTA) if en_preguntas else SIN_CONTENIDO
            elif "no es una descripción" in motivo:
                aviso = NO_RESPONDE if en_preguntas else NO_DESCRIPCION
            elif "foto" in motivo or "en curso" in motivo:
                self._tg.enviar(chat, FOTO_NO_DISPONIBLE if "foto" in motivo else NADA_EN_CURSO,
                                teclado=QUITAR_TECLADO)
                return
            else:
                return
            if en_preguntas:
                self._tg.enviar(chat, aviso, mensaje["message_id"])
                self._preguntar(chat, pregunta)
            else:
                self._tg.enviar(chat, aviso, mensaje["message_id"], teclado=TECLADO_DESCRIPCION)
            return
        if tipo == "audio" and contenido and self._transcriptor:
            self._avisar[resultado["id"]] = (chat, mensaje["message_id"], persona)
            self._transcriptor.encolar(resultado["id"], lambda c=contenido: c)
            self._tg.enviar(chat, AUDIO_GUARDADO_PREGUNTA if en_preguntas else AUDIO_GUARDADO, mensaje["message_id"])
            return
        if en_preguntas:
            # Respondió la pregunta: va la siguiente (o la pregunta abierta del final).
            antes = ANOTADO.get(resultado.get("estado_respuesta"), ANOTADO["respondida"])
            if es_de_persona(actual) and not es_de_persona(pregunta) and not pregunta.get("cierre"):
                antes = f"{antes}\n\n{AHORA_FOTO}"  # terminó las preguntas sobre la persona
            self._preguntar(chat, pregunta, antes)
            return
        self._tg.enviar(chat, GUARDADO, mensaje["message_id"], teclado=TECLADO_DESCRIPCION)

    def transcrito(self, id_: int, texto: str | None, estado: str) -> None:
        """Lo llama el transcriptor al terminar un audio: guarda la transcripción
        y, si el audio llegó en privado, le dice a la persona si quedó guardado y sigue
        con la pregunta que toca. No le muestra la transcripción: la revisa el equipo."""
        resultado = self._grupos.transcripcion(id_, texto, estado)
        destino = self._avisar.pop(id_, None)
        if not destino:
            return
        chat, audio, persona = destino
        codigo = resultado.get("pregunta_codigo")
        es_pregunta = bool(codigo) and codigo != "ADICIONAL"
        # Sin mostrarle a la persona lo que entendió Whisper.
        if estado == "lista" and texto:
            especial = {"no_sabe": "No sé", "no_aplica": "No aplica",
                        "no_responde": "Prefiero no responder"}.get(resultado.get("estado_respuesta") or "")
            if especial and es_pregunta:
                aviso = ANOTADO.get(resultado.get("estado_respuesta"), AUDIO_ANOTADO)
            elif resultado.get("es_descripcion") is False:
                aviso = AUDIO_NO_RESPONDE if es_pregunta else AUDIO_NO_DESCRIPCION
            else:
                aviso = AUDIO_ANOTADO if es_pregunta else AUDIO_LISTO
        elif estado == "vacia":
            aviso = NO_ENTENDI
        else:
            aviso = NO_TRANSCRITO
        try:
            self._tg.enviar(chat, aviso, audio)
            if es_pregunta:
                # Sigue con la pregunta que toca: la siguiente, o la misma si el audio no la respondió.
                en_curso = self.en_curso(persona)
                if en_curso:
                    siguiente = en_curso.get("pregunta") or {}
                    cambio = (resultado.get("pregunta_alcance") == "persona" and not es_de_persona(siguiente)
                              and not siguiente.get("cierre"))
                    self._preguntar(chat, siguiente, AHORA_FOTO if cambio else "")
        except Exception:
            logger.exception("No se pudo avisar la transcripción del audio %s", id_)

    def revisar_privado(self, mensaje: dict, orden: str, argumento: str) -> None:
        """/transcripcion <registro> y /corregir <registro> <texto> en el chat privado:
        solo para los administradores del grupo de ese registro (el equipo). Quien
        describe no ve ni corrige sus transcripciones, para que no las cambie."""
        chat = mensaje["chat"]["id"]
        persona, nombre = autor(mensaje)
        partes = argumento.strip().split(" ", 1)
        if not partes[0].isdigit():
            self._tg.enviar(chat, SOLO_EQUIPO, mensaje["message_id"])
            return
        try:
            datos = self._grupos.registro(int(partes[0]))
        except Exception:
            datos = None
        if not datos or datos.get("tipo") != "audio":
            self._tg.enviar(chat, NO_ES_AUDIO, mensaje["message_id"])
            return
        if not self._es_admin(datos["chat_id"], persona):
            self._tg.enviar(chat, NO_ES_ADMIN_REGISTRO, mensaje["message_id"])
            return
        if orden == "/corregir":
            texto = partes[1].strip() if len(partes) > 1 else ""
            if not texto:
                self._tg.enviar(chat, USO_EQUIPO, mensaje["message_id"])
                return
            self._grupos.corregir_registro(datos["id"], texto, nombre)
            self._tg.enviar(chat, CORREGIDO, mensaje["message_id"])
            return
        pregunta = f"Pregunta {datos['pregunta_codigo']}: {datos.get('pregunta_texto') or ''}\n\n" if datos.get(
            "pregunta_codigo") else ""
        texto = (pregunta + texto_transcripcion(datos)).replace(
            "responde al audio con /corregir <lo que realmente dijo>", f"escribe /corregir {datos['id']} <lo que realmente dijo>")
        self._tg.enviar(chat, texto, mensaje["message_id"])
        if datos.get("audio_disponible"):
            try:
                self._tg.enviar_archivo(chat, f"audio-{datos['id']}.ogg", self._grupos.archivo(datos["id"]),
                                        f"Audio del registro {datos['id']}")
            except Exception:
                logger.exception("No se pudo enviar el audio %s", datos["id"])
