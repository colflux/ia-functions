"""Cliente mínimo de la API de bots de Telegram (https://core.telegram.org/bots/api)."""
import httpx

API = "https://api.telegram.org"
ESPERA_LARGA = 50  # segundos que Telegram retiene getUpdates si no hay mensajes
MAX_TEXTO = 4096


class Telegram:
    def __init__(self, token: str) -> None:
        self._base = f"{API}/bot{token}"
        self._archivos = f"{API}/file/bot{token}"
        self._http = httpx.Client(timeout=ESPERA_LARGA + 10)

    def _llamar(self, metodo: str, **kwargs) -> dict | list | bool:
        respuesta = self._http.post(f"{self._base}/{metodo}", **kwargs)
        datos = respuesta.json()
        if not datos.get("ok"):
            raise RuntimeError(f"{metodo}: {datos.get('description', respuesta.status_code)}")
        return datos["result"]

    def yo(self) -> dict:
        return self._llamar("getMe")

    def mensajes_nuevos(self, desde: int | None) -> list[dict]:
        return self._llamar("getUpdates", json={"offset": desde, "timeout": ESPERA_LARGA,
                                                "allowed_updates": ["message"]})

    def escribiendo(self, chat_id: int) -> None:
        self._llamar("sendChatAction", json={"chat_id": chat_id, "action": "typing"})

    def enviar(self, chat_id: int, texto: str, responde_a: int | None = None) -> None:
        """Texto plano; si pasa del límite de Telegram, en varios mensajes."""
        for parte in partir(texto, MAX_TEXTO):
            cuerpo = {"chat_id": chat_id, "text": parte, "link_preview_options": {"is_disabled": True}}
            if responde_a:
                cuerpo["reply_parameters"] = {"message_id": responde_a, "allow_sending_without_reply": True}
            self._llamar("sendMessage", json=cuerpo)

    def enviar_archivo(self, chat_id: int, nombre: str, contenido: bytes, leyenda: str) -> None:
        self._llamar("sendDocument", data={"chat_id": chat_id, "caption": leyenda},
                     files={"document": (nombre, contenido)})


    def descargar(self, file_id: str) -> bytes:
        """Descarga un archivo que llegó en un mensaje (foto, audio). Telegram
        solo deja a los bots descargar archivos de hasta 20 MB."""
        ruta = self._llamar("getFile", json={"file_id": file_id})["file_path"]
        respuesta = self._http.get(f"{self._archivos}/{ruta}")
        respuesta.raise_for_status()
        return respuesta.content

    def es_admin(self, chat_id: int, persona: int) -> bool:
        miembro = self._llamar("getChatMember", json={"chat_id": chat_id, "user_id": persona})
        return miembro.get("status") in ("creator", "administrator")


def partir(texto: str, limite: int) -> list[str]:
    """Corta por párrafos o líneas, nunca a mitad de palabra si se puede evitar."""
    partes = []
    while len(texto) > limite:
        corte = max(texto.rfind("\n", 0, limite), texto.rfind(" ", 0, limite))
        corte = corte if corte > limite // 2 else limite
        partes.append(texto[:corte].rstrip())
        texto = texto[corte:].lstrip()
    return partes + ([texto] if texto else [])
