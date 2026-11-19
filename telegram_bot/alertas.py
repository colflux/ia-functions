"""Aviso cuando el bot lleva un rato sin poder leer Telegram (y cuando se recupera).

Telegram a veces responde 502 a un bot concreto durante horas (le pasó a
@COLFLUXBOT el 17 de noviembre de 2026): el bot reintenta solo, pero nadie se
enteraba hasta que los participantes notaban la demora. Tras ALERTA_MINUTOS
minutos fallando seguidos (10 por defecto) deja en el registro una línea
«ALERTA TELEGRAM» y, si hay correo configurado, manda un correo; al volver,
«TELEGRAM RECUPERADO». Un solo aviso por caída.

Correo (opcional, variables del servicio telegram): ALERTA_CORREOS (separados
por coma), ALERTA_SMTP_HOST (smtp.gmail.com), ALERTA_SMTP_PUERTO (587),
ALERTA_SMTP_USUARIO y ALERTA_SMTP_CLAVE. Sin ellas, solo queda el registro.
"""
import logging
import os
import smtplib
import threading
import time
from email.message import EmailMessage

logger = logging.getLogger("telegram_bot.alertas")


class AlertaConexion:
    def __init__(self, minutos: float | None = None) -> None:
        self._minutos = float(minutos if minutos is not None else os.environ.get("ALERTA_MINUTOS", "10") or 10)
        self._fallando_desde: float | None = None
        self._alertado = False
        self._ultimo_error = ""
        self._correos = [c.strip() for c in os.environ.get("ALERTA_CORREOS", "").split(",") if c.strip()]

    def fallo(self, error: object) -> None:
        """Lo llama el bot cada vez que no pudo leer Telegram."""
        ahora = time.monotonic()
        self._ultimo_error = str(error)[:300]
        if self._fallando_desde is None:
            self._fallando_desde = ahora
        minutos = (ahora - self._fallando_desde) / 60
        if not self._alertado and minutos >= self._minutos:
            self._alertado = True
            texto = (f"El bot de Telegram lleva {minutos:.0f} minutos sin poder leer los mensajes. "
                     f"Último error: {self._ultimo_error}. Sigue reintentando solo; los mensajes "
                     "quedan guardados en Telegram hasta 24 horas.")
            logger.error("ALERTA TELEGRAM: %s", texto)
            self._correo("COLFLUX: el bot de Telegram no puede leer mensajes", texto)

    def ok(self) -> None:
        """Lo llama el bot cuando leyó Telegram sin problema."""
        if self._fallando_desde is None:
            return
        minutos = (time.monotonic() - self._fallando_desde) / 60
        if self._alertado:
            texto = f"El bot de Telegram volvió a leer los mensajes después de {minutos:.0f} minutos."
            logger.warning("TELEGRAM RECUPERADO: %s", texto)
            self._correo("COLFLUX: el bot de Telegram se recuperó", texto)
        self._fallando_desde = None
        self._alertado = False

    def _correo(self, asunto: str, texto: str) -> None:
        usuario, clave = os.environ.get("ALERTA_SMTP_USUARIO", ""), os.environ.get("ALERTA_SMTP_CLAVE", "")
        if not (self._correos and usuario and clave):
            return
        threading.Thread(target=self._enviar, args=(asunto, texto, usuario, clave), daemon=True).start()

    def _enviar(self, asunto: str, texto: str, usuario: str, clave: str) -> None:
        mensaje = EmailMessage()
        mensaje["Subject"], mensaje["From"], mensaje["To"] = asunto, usuario, ", ".join(self._correos)
        mensaje.set_content(texto)
        try:
            with smtplib.SMTP(os.environ.get("ALERTA_SMTP_HOST", "smtp.gmail.com"),
                              int(os.environ.get("ALERTA_SMTP_PUERTO", "587")), timeout=20) as smtp:
                smtp.starttls()
                smtp.login(usuario, clave)
                smtp.send_message(mensaje)
            logger.info("Aviso enviado por correo a %s", ", ".join(self._correos))
        except Exception:
            logger.exception("No se pudo enviar el aviso por correo")
