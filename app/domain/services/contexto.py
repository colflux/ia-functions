"""Datos de la petición en curso que las herramientas necesitan y el modelo no
debe ver: la sesión de quien escribe. El chat la envía con cada mensaje; la
ruta /chat la deja aquí y las herramientas de escritura la leen para verificar
con el backend el nivel de la persona."""
from contextvars import ContextVar

autorizacion_actual: ContextVar[str | None] = ContextVar("autorizacion_actual", default=None)
