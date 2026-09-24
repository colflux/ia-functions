from typing import Any

import httpx

from app.domain.ports.user_directory import UserDirectory


class BackendUserDirectory(UserDirectory):
    """Pregunta al backend de COLFLUX de quién es un token de sesión.

    El nivel de acceso lo decide el backend: el asistente nunca confía en lo
    que el navegador diga sobre el usuario.
    """

    def __init__(self, base_url: str, timeout: float = 10.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def usuario_por_token(self, autorizacion: str) -> dict[str, Any] | None:
        if not autorizacion.startswith("Token "):
            return None
        respuesta = httpx.get(
            f"{self._base_url}/api/auth/me/",
            headers={"Authorization": autorizacion},
            timeout=self._timeout,
        )
        if respuesta.status_code in (401, 403):
            return None
        if respuesta.status_code == 404:
            # Cuenta de acceso sin usuario de dominio: existe, pero sin nivel.
            return {}
        respuesta.raise_for_status()
        return respuesta.json()
