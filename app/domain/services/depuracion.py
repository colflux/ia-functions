"""Quita del índice los archivos subidos que ya no están en el bucket.

Si alguien borra un archivo desde la consola del bucket, sus fragmentos seguían
en la base del asistente y el chat lo citaba como si existiera. Antes de
consultar archivos subidos se comprueba, como mucho una vez cada pocos minutos,
qué fuentes ya no tienen original, y se borran sus fragmentos.
"""
import logging
import threading
import time

from app.domain.ports.file_storage import FileStorage
from app.domain.ports.vector_store import VectorStore

logger = logging.getLogger("uvicorn.error")

PREFIJO = "documentos/"
INTERVALO = 300  # segundos entre revisiones


class DepuradorArchivos:
    def __init__(self, store: VectorStore, almacen: FileStorage | None, intervalo: int = INTERVALO) -> None:
        self._store = store
        self._almacen = almacen
        self._intervalo = intervalo
        self._ultima = 0.0
        self._candado = threading.Lock()

    def depurar(self, forzar: bool = False) -> int:
        """Número de archivos retirados del índice."""
        if self._almacen is None:
            return 0
        with self._candado:
            if not forzar and time.monotonic() - self._ultima < self._intervalo:
                return 0
            self._ultima = time.monotonic()
        retirados = 0
        try:
            for fuente in self._store.fuentes(PREFIJO):
                if not self._almacen.existe(fuente):
                    borrados = self._store.borrar_fuente(fuente)
                    retirados += 1
                    logger.info("DEPURACION %s ya no está en el bucket: %d fragmentos retirados", fuente, borrados)
        except Exception:
            # Si el bucket no responde, mejor no borrar nada.
            logger.exception("DEPURACION no se pudo revisar el bucket")
        return retirados
