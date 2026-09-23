import boto3

from app.domain.ports.file_storage import FileStorage


class LightsailBucket(FileStorage):
    """Bucket de Lightsail. Habla el mismo protocolo que S3."""

    def __init__(self, nombre: str, region: str, access_key_id: str, secret_access_key: str):
        self._nombre = nombre
        self._cliente = boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )

    def subir(self, clave: str, contenido: bytes, tipo_contenido: str, metadatos: dict[str, str]) -> None:
        self._cliente.put_object(
            Bucket=self._nombre,
            Key=clave,
            Body=contenido,
            ContentType=tipo_contenido,
            Metadata=metadatos,
        )

    def borrar(self, clave: str) -> None:
        self._cliente.delete_object(Bucket=self._nombre, Key=clave)
