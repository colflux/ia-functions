"""Descarga de páginas públicas (la wiki del proyecto y sus PDF)."""
import httpx

TIMEOUT = 30
MAX_BYTES = 25 * 1024 * 1024


def descargar(url: str) -> bytes:
    """Contenido de `url`. Cualquier fallo de red o respuesta de error se
    convierte en OSError, para que quien llama no dependa de httpx."""
    try:
        with httpx.stream("GET", url, timeout=TIMEOUT, follow_redirects=True) as respuesta:
            respuesta.raise_for_status()
            partes, total = [], 0
            for parte in respuesta.iter_bytes():
                total += len(parte)
                if total > MAX_BYTES:
                    raise OSError(f"{url} supera {MAX_BYTES // (1024 * 1024)} MB")
                partes.append(parte)
    except httpx.HTTPError as exc:
        raise OSError(f"{url}: {exc}") from exc
    return b"".join(partes)
