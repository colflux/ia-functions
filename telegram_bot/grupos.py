"""Cliente de las rutas /grupos del asistente (internas, con X-Grupos-Token)."""
import json

import httpx

TIMEOUT = 60


class Grupos:
    def __init__(self, url: str, token: str) -> None:
        self._url = url.rstrip("/") + "/grupos"
        self._http = httpx.Client(timeout=TIMEOUT, headers={"X-Grupos-Token": token})

    def _ok(self, respuesta: httpx.Response) -> dict | list:
        respuesta.raise_for_status()
        return respuesta.json()

    def registrar(self, chat_id: int, nombre: str, sitio: str, registrado_por: str) -> dict:
        return self._ok(self._http.post(f"{self._url}/registrar", json={
            "chat_id": chat_id, "nombre": nombre, "sitio": sitio, "registrado_por": registrado_por}))

    def pausar(self, chat_id: int) -> dict:
        return self._ok(self._http.post(f"{self._url}/{chat_id}/pausar"))

    def estado(self, chat_id: int) -> dict:
        return self._ok(self._http.get(f"{self._url}/{chat_id}/estado"))

    def resumen(self, chat_id: int) -> dict:
        return self._ok(self._http.get(f"{self._url}/{chat_id}/resumen"))

    def guardar(self, datos: dict, archivo: tuple[str, bytes, str] | None = None) -> dict:
        files = {"archivo": archivo} if archivo else None
        return self._ok(self._http.post(f"{self._url}/mensajes", data={"datos": json.dumps(datos)}, files=files))

    def mensaje(self, chat_id: int, message_id: int) -> dict:
        return self._ok(self._http.get(f"{self._url}/{chat_id}/mensajes/{message_id}"))

    def corregir(self, chat_id: int, message_id: int, texto: str, por: str) -> dict:
        return self._ok(self._http.post(f"{self._url}/{chat_id}/mensajes/{message_id}/correccion",
                                        json={"texto": texto, "por": por}))

    def datos_foto(self, chat_id: int, message_id: int, sitio: str, fecha: str, luz: str | None) -> dict:
        return self._ok(self._http.post(f"{self._url}/{chat_id}/fotos/{message_id}/datos",
                                        json={"sitio": sitio, "fecha": fecha, "luz": luz}))

    def ubicacion_persona(self, datos: dict) -> dict:
        return self._ok(self._http.post(f"{self._url}/ubicaciones", json=datos))

    def audios_pendientes(self) -> list[dict]:
        return self._ok(self._http.get(f"{self._url}/audios/pendientes"))

    def archivo(self, id_: int) -> bytes:
        respuesta = self._http.get(f"{self._url}/archivos/{id_}")
        respuesta.raise_for_status()
        return respuesta.content

    def transcripcion(self, id_: int, texto: str | None, estado: str) -> dict:
        return self._ok(self._http.post(f"{self._url}/transcripcion", json={"id": id_, "texto": texto, "estado": estado}))

    # ── descripciones en privado ──────────────────────────────────────────
    def iniciar_descripcion(self, autor_id: int, autor_nombre: str, chat_privado: int, foto: int) -> dict:
        respuesta = self._http.post(f"{self._url}/privado/descripcion", json={
            "autor_id": autor_id, "autor_nombre": autor_nombre, "chat_privado": chat_privado, "foto": foto})
        if respuesta.status_code in (404, 409):
            return {"ok": False, "motivo": respuesta.json().get("detail", "")}
        return self._ok(respuesta)

    def descripcion_en_curso(self, autor_id: int) -> dict:
        return self._ok(self._http.get(f"{self._url}/privado/descripcion/{autor_id}"))

    def terminar_descripcion(self, autor_id: int) -> dict:
        return self._ok(self._http.delete(f"{self._url}/privado/descripcion/{autor_id}"))

    def mensaje_privado(self, chat_origen: int, message_id: int) -> dict:
        return self._ok(self._http.get(f"{self._url}/privado/{chat_origen}/mensajes/{message_id}"))

    def corregir_privado(self, chat_origen: int, message_id: int, texto: str, por: str) -> dict:
        return self._ok(self._http.post(f"{self._url}/privado/{chat_origen}/mensajes/{message_id}/correccion",
                                        json={"texto": texto, "por": por}))

    def detallar_fotos(self) -> dict:
        return self._ok(self._http.post(f"{self._url}/fotos/detallar"))

    def registro(self, id_: int) -> dict | None:
        respuesta = self._http.get(f"{self._url}/registros/{id_}")
        if respuesta.status_code == 404:
            return None
        return self._ok(respuesta)

    def corregir_registro(self, id_: int, texto: str, por: str) -> dict:
        return self._ok(self._http.post(f"{self._url}/registros/{id_}/correccion", json={"texto": texto, "por": por}))

    def depurar_audios(self) -> dict:
        return self._ok(self._http.post(f"{self._url}/depurar-audios"))
