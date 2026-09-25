import boto3
from botocore.exceptions import ClientError

from app.domain.ports.file_storage import FileStorage


class LightsailBucket(FileStorage):
    """Bucket de Lightsail. Habla el mismo protocolo que S3.

    `prefijo` separa entornos que comparten bucket: con BUCKET_PREFIJO=lab/ el
    laboratorio guarda todo bajo lab/ y no ve ni pisa lo de producción (antes una
    huella del laboratorio le decía a producción que un archivo «ya se había
    subido»). Las claves que ven el resto del código y la base no cambian."""

    def __init__(self, nombre: str, region: str, access_key_id: str, secret_access_key: str,
                 prefijo: str = ""):
        self._nombre = nombre
        self._prefijo = prefijo.strip("/") + "/" if prefijo.strip("/") else ""
        self._cliente = boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )

    def subir(self, clave: str, contenido: bytes, tipo_contenido: str, metadatos: dict[str, str]) -> None:
        self._cliente.put_object(
            Bucket=self._nombre,
            Key=self._prefijo + clave,
            Body=contenido,
            ContentType=tipo_contenido,
            Metadata=metadatos,
        )

    def existe(self, clave: str) -> bool:
        try:
            self._cliente.head_object(Bucket=self._nombre, Key=self._prefijo + clave)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound"):
                return False
            raise

    def leer(self, clave: str) -> bytes | None:
        try:
            return self._cliente.get_object(Bucket=self._nombre, Key=self._prefijo + clave)["Body"].read()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise

    def enlace_descarga(self, clave: str, nombre: str, segundos: int, en_linea: bool = False) -> str | None:
        if not self.existe(clave):
            return None
        return self._cliente.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._nombre,
                "Key": self._prefijo + clave,
                "ResponseContentDisposition": f'{"inline" if en_linea else "attachment"}; filename="{nombre}"',
            },
            ExpiresIn=segundos,
        )

    def borrar(self, clave: str) -> None:
        self._cliente.delete_object(Bucket=self._nombre, Key=self._prefijo + clave)
