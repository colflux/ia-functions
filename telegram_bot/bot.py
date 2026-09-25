"""Bot de Telegram del asistente de COLFLUX.

Cada mensaje de texto se envía al mismo /chat que usa la web, así que el bot
responde igual: mismos datos, diccionario, wiki y reglas. Si la respuesta trae
un Excel, se envía como archivo. En esta versión no se suben archivos ni se
registran mediciones: eso exige una cuenta de COLFLUX y se hace desde la web.
"""
import logging
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor

from telegram_bot.asistente import Asistente
from telegram_bot.telegram import Telegram

logger = logging.getLogger("telegram_bot")

HILOS = 4
MAX_FUENTES = 5
PLATAFORMA = "http://44.213.47.34/mapas"

BIENVENIDA = (
    "Hola, soy el asistente de COLFLUX, el proyecto sobre el carbono en páramos, "
    "humedales, sabanas inundables y morichales de Colombia.\n\n"
    "Pregúntame por las mediciones de gases (CO2, CH4, N2O) de un sitio, vereda o "
    "municipio, por lo que significa algo que observaste en campo (un olor, un color "
    "del suelo) o por el proyecto. Si te doy datos, te los puedo enviar en Excel.\n\n"
    f"Para subir archivos o registrar mediciones usa la plataforma: {PLATAFORMA}"
)
SOLO_TEXTO = ("Por ahora aquí solo respondo mensajes de texto. Para subir fotos o archivos "
              f"usa la plataforma: {PLATAFORMA}")
NO_DISPONIBLE = "El asistente no está disponible en este momento. Intenta de nuevo en unos minutos."
SIN_EXCEL = "No pude preparar el Excel. Pídemelo de nuevo en unos minutos."


class LimitePorPersona:
    """Como mucho `maximo` mensajes por persona en la última hora, para no agotar
    la cuota de los proveedores de IA."""

    def __init__(self, maximo: int) -> None:
        self._maximo = maximo
        self._horas: dict[int, deque] = defaultdict(deque)
        self._candado = threading.Lock()

    def permite(self, persona: int) -> bool:
        ahora = time.monotonic()
        with self._candado:
            horas = self._horas[persona]
            while horas and ahora - horas[0] > 3600:
                horas.popleft()
            if len(horas) >= self._maximo:
                return False
            horas.append(ahora)
            return True


class Bot:
    def __init__(self, telegram: Telegram, asistente: Asistente, mensajes_por_hora: int) -> None:
        self._tg = telegram
        self._asistente = asistente
        self._limite = LimitePorPersona(mensajes_por_hora)
        self._mensajes_por_hora = mensajes_por_hora
        self._candados: dict[int, threading.Lock] = defaultdict(threading.Lock)
        self._nombre = ""

    def correr(self) -> None:
        self._nombre = "@" + self._tg.yo()["username"]
        logger.info("Bot %s escuchando", self._nombre)
        desde = None
        with ThreadPoolExecutor(HILOS) as hilos:
            while True:
                try:
                    novedades = self._tg.mensajes_nuevos(desde)
                except Exception:
                    logger.exception("No se pudo leer Telegram; reintento en 10 s")
                    time.sleep(10)
                    continue
                for novedad in novedades:
                    desde = novedad["update_id"] + 1
                    if "message" in novedad:
                        hilos.submit(self._atender_en_orden, novedad["message"])

    def _atender_en_orden(self, mensaje: dict) -> None:
        # Los mensajes de un mismo chat se responden en el orden en que llegaron.
        with self._candados[mensaje["chat"]["id"]]:
            try:
                self._atender(mensaje)
            except Exception:
                logger.exception("Error atendiendo el chat %s", mensaje["chat"]["id"])

    def _atender(self, mensaje: dict) -> None:
        chat = mensaje["chat"]["id"]
        persona = (mensaje.get("from") or {}).get("id", chat)
        privado = mensaje["chat"].get("type") == "private"
        texto = (mensaje.get("text") or "").strip()

        if not privado:
            # En grupos solo se responde si lo mencionan o le contestan al bot.
            respondido = (mensaje.get("reply_to_message") or {}).get("from", {}).get("username")
            if self._nombre.lower() not in texto.lower() and f"@{respondido}".lower() != self._nombre.lower():
                return
            texto = texto.replace(self._nombre, "").strip()

        if texto.split(" ")[0].split("@")[0] in ("/start", "/ayuda", "/help"):
            self._tg.enviar(chat, BIENVENIDA)
            return
        if not texto:
            if privado:
                self._tg.enviar(chat, SOLO_TEXTO)
            return
        if not self._limite.permite(persona):
            self._tg.enviar(chat, f"Llegaste al máximo de {self._mensajes_por_hora} mensajes por hora. "
                                  "Intenta de nuevo más tarde.", mensaje["message_id"])
            return

        self._tg.escribiendo(chat)
        try:
            respuesta = self._asistente.preguntar(texto, f"telegram-{persona}")
        except Exception:
            logger.exception("El asistente no respondió")
            self._tg.enviar(chat, NO_DISPONIBLE)
            return
        responde_a = None if privado else mensaje["message_id"]
        self._tg.enviar(chat, respuesta["answer"] + _fuentes(respuesta.get("sources") or []), responde_a)
        for excel in respuesta.get("descargas") or []:
            self._enviar_excel(chat, excel)

    def _enviar_excel(self, chat: int, excel: dict) -> None:
        try:
            contenido = self._asistente.excel(excel["archivo"])
        except Exception:
            logger.exception("No se pudo descargar el Excel %s", excel.get("archivo"))
            self._tg.enviar(chat, SIN_EXCEL)
            return
        filas = excel.get("filas")
        leyenda = f"{filas} filas" if filas is not None else ""
        self._tg.enviar_archivo(chat, excel.get("nombre") or "colflux.xlsx", contenido, leyenda)


def _fuentes(sources: list[dict]) -> str:
    """Solo las fuentes que se pueden abrir (páginas de la wiki y sus PDF). Los
    archivos subidos se descargan con sesión en la plataforma y el diccionario
    no es un enlace."""
    enlaces = list(dict.fromkeys(s["source"] for s in sources if s["source"].startswith("http")))
    if not enlaces:
        return ""
    return "\n\nFuentes:\n" + "\n".join(enlaces[:MAX_FUENTES])
