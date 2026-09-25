"""python -m telegram_bot

Variables: TELEGRAM_BOT_TOKEN (sin token no arranca), ASISTENTE_URL y
TELEGRAM_MENSAJES_POR_HORA."""
import logging
import os
import sys

from telegram_bot.asistente import Asistente
from telegram_bot.bot import Bot
from telegram_bot.telegram import Telegram


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # httpx registra cada petición con la URL, que lleva el token del bot.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logging.info("Sin TELEGRAM_BOT_TOKEN: el bot de Telegram no se inicia.")
        return 0
    bot = Bot(Telegram(token), Asistente(os.environ.get("ASISTENTE_URL", "http://api:8000")),
              int(os.environ.get("TELEGRAM_MENSAJES_POR_HORA", "20")))
    bot.correr()
    return 0


if __name__ == "__main__":
    sys.exit(main())
