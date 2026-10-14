"""python -m telegram_bot

Variables: TELEGRAM_BOT_TOKEN (sin token no arranca), ASISTENTE_URL y
TELEGRAM_MENSAJES_POR_HORA. Grupos de observación: GRUPOS_TOKEN (sin él no se
registran grupos), WHISPER_MODELO (small; vacío = no transcribir),
WHISPER_CARPETA y WHISPER_HILOS."""
import logging
import os
import sys

from telegram_bot.asistente import Asistente
from telegram_bot.bot import Bot
from telegram_bot.grupos import Grupos
from telegram_bot.registro_grupos import RegistroGrupos
from telegram_bot.telegram import Telegram
from telegram_bot.transcriptor import Transcriptor


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # httpx registra cada petición con la URL, que lleva el token del bot.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logging.info("Sin TELEGRAM_BOT_TOKEN: el bot de Telegram no se inicia.")
        return 0
    url = os.environ.get("ASISTENTE_URL", "http://api:8000")
    telegram = Telegram(token)
    grupos = None
    token_grupos = os.environ.get("GRUPOS_TOKEN", "").strip()
    if token_grupos:
        cliente = Grupos(url, token_grupos)
        modelo = os.environ.get("WHISPER_MODELO", "small").strip()
        transcriptor = None
        if modelo:
            transcriptor = Transcriptor(modelo, os.environ.get("WHISPER_CARPETA", "/modelos"),
                                        int(os.environ.get("WHISPER_HILOS", "2")),
                                        lambda id_, texto, estado: cliente.transcripcion(id_, texto, estado))
        grupos = RegistroGrupos(telegram, cliente, transcriptor)
    else:
        logging.info("Sin GRUPOS_TOKEN: los grupos de observación están desactivados.")
    bot = Bot(telegram, Asistente(url), int(os.environ.get("TELEGRAM_MENSAJES_POR_HORA", "20")), grupos)
    bot.correr()
    return 0


if __name__ == "__main__":
    sys.exit(main())
