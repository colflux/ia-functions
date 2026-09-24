"""Coordenadas GPS guardadas dentro de una foto (EXIF).

Los celulares las escriben si la ubicación está activada al tomar la foto. Es la
ubicación más precisa de lo que se ve en la imagen, porque es donde se tomó; la
del navegador es donde está la persona al subirla, que puede ser su oficina."""
import io
import logging

logger = logging.getLogger("uvicorn.error")

GPS_IFD = 0x8825  # bloque GPSInfo del EXIF


def _grados(valor, referencia: str) -> float:
    g, m, s = (float(x) for x in valor)
    decimal = g + m / 60 + s / 3600
    return -decimal if referencia in ("S", "W") else decimal


def coordenadas_de_foto(contenido: bytes) -> tuple[float, float] | None:
    """(latitud, longitud) o None si la foto no trae GPS o no se puede leer."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(contenido)) as imagen:
            gps = imagen.getexif().get_ifd(GPS_IFD)
        if not gps or 2 not in gps or 4 not in gps:
            return None
        lat = _grados(gps[2], str(gps.get(1, "N")))
        lon = _grados(gps[4], str(gps.get(3, "E")))
        if lat == 0 and lon == 0:
            return None
        return round(lat, 6), round(lon, 6)
    except Exception:
        logger.info("GPS de la foto no legible", exc_info=True)
        return None
