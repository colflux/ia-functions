"""Copia al prefijo de este entorno (BUCKET_PREFIJO) los archivos subidos que su
base tiene indexados, para dejar de compartir claves con otro entorno.

Uso, dentro del contenedor del asistente del laboratorio, ANTES de poner
BUCKET_PREFIJO en su .env:

    python -m app.scripts.mover_al_prefijo lab/            # muestra qué haría
    python -m app.scripts.mover_al_prefijo lab/ --aplicar  # copia

Por cada fuente documentos/... de esta base copia el original y su descripción
(documentos/_info/...) a <prefijo>/..., y escribe la huella bajo el prefijo. Solo
copia: no borra nada del bucket.
"""
import hashlib
import mimetypes
import sys

from app.adapters.storage.lightsail_bucket import LightsailBucket
from app.adapters.vectorstore.pgvector_store import PgVectorStore
from app.config import settings
from app.domain.services.carga_documentos import PREFIJO_DOCUMENTOS, PREFIJO_HUELLAS, PREFIJO_INFO


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    prefijo, aplicar = sys.argv[1], "--aplicar" in sys.argv
    origen = LightsailBucket(settings.bucket_name, settings.bucket_region, settings.bucket_access_key_id,
                             settings.bucket_secret_access_key)
    destino = LightsailBucket(settings.bucket_name, settings.bucket_region, settings.bucket_access_key_id,
                              settings.bucket_secret_access_key, prefijo)
    fuentes = sorted(PgVectorStore().fuentes(PREFIJO_DOCUMENTOS))
    print(f"{len(fuentes)} archivos indexados en esta base.")
    for clave in fuentes:
        contenido = origen.leer(clave)
        if contenido is None:
            print(f"  (sin original en el bucket, se omite) {clave}")
            continue
        clave_info = f"{PREFIJO_INFO}{clave[len(PREFIJO_DOCUMENTOS):]}.txt"
        info = origen.leer(clave_info)
        print(f"  {clave}  ({len(contenido)} bytes{', con descripción' if info else ''})")
        if not aplicar:
            continue
        destino.subir(clave, contenido, mimetypes.guess_type(clave)[0] or "application/octet-stream", {})
        if info is not None:
            destino.subir(clave_info, info, "text/plain; charset=utf-8", {})
        huella = hashlib.sha256(contenido).hexdigest()
        registro = origen.leer(f"{PREFIJO_HUELLAS}{huella}")
        if registro is not None:
            destino.subir(f"{PREFIJO_HUELLAS}{huella}", registro, "text/plain; charset=utf-8", {})
    print("Listo: copiados." if aplicar else "No se copió nada. Para copiar, repite con --aplicar.")


if __name__ == "__main__":
    main()
