"""Cliente del asistente de COLFLUX (el mismo /chat que usa la web)."""
import httpx

TIMEOUT = 180  # una respuesta con varias herramientas puede tardar


class Asistente:
    def __init__(self, url: str) -> None:
        self._url = url.rstrip("/")
        self._http = httpx.Client(timeout=TIMEOUT)

    def preguntar(self, mensaje: str, usuario: str) -> dict:
        respuesta = self._http.post(f"{self._url}/chat", json={"message": mensaje, "usuario": usuario})
        respuesta.raise_for_status()
        return respuesta.json()

    def excel(self, archivo: str) -> bytes:
        enlace = self._http.get(f"{self._url}/descargas/excel", params={"archivo": archivo})
        enlace.raise_for_status()
        contenido = self._http.get(enlace.json()["url"])
        contenido.raise_for_status()
        return contenido.content
