"""Mapeo de columnas asistido por IA para el Formulario web (ver
personal/tasks/backlog/formulario-web-carga-datos-ia.md en `context`).

`proponer_mapeo_columnas` solo trae las columnas del archivo y el catálogo
de campos destino — es el propio modelo, en la conversación, quien razona
el mapeo y lo presenta a la persona. `confirmar_mapeo_columnas` recién
escribe en el backend, una vez la persona lo aprueba en el chat.

`token` lo inyecta `agent_orchestrator.py` justo antes de ejecutar la tool
(ver AUTH_REQUIRED_TOOLS) — nunca lo completa el modelo, aunque aparezca en
el schema de la tool por ser un parámetro más de la función."""

from mcp_server import backend_client

# El catálogo completo (verbose_name, choices, max_length, etc. de cada
# campo de los ~15 modelos) pesa ~35 KB — sumado al system prompt y al
# listado de tools, supera el límite de 8000 TPM del tier on-demand de Groq
# (visto en vivo: "Request too large... Requested 12661"). Para decidir a
# qué campo corresponde cada columna del archivo el modelo solo necesita
# nombre/tipo/si es FK — se resume acá, no en el backend, porque el wizard
# manual sí necesita el catálogo completo (choices, verbose_name, etc.).
def _resumir_catalogo(modelos: dict) -> dict:
    return {
        modelo: [
            {
                "nombre": campo["nombre"],
                "tipo": campo["tipo"],
                "requerido": campo["requerido"],
                "es_fk": campo["es_fk"],
                "modelo_fk": campo["modelo_fk"],
            }
            for campo in campos
        ]
        for modelo, campos in modelos.items()
    }


def proponer_mapeo_columnas(carga_id: int, token: str | None = None) -> dict:
    """Trae las columnas de una carga del Formulario web (creada al subir
    un archivo) y el catálogo de campos del modelo de datos de colflux, para
    que el propio modelo proponga en el chat a qué campo del modelo destino
    corresponde cada columna del archivo."""
    if not token:
        return {"error": "No hay sesión de usuario para consultar esta carga."}

    carga = backend_client.get_carga_ia(carga_id, token=token)
    if "error" in carga:
        return carga

    catalogo = backend_client.get_campos_destino()
    return {
        "carga_id": carga["carga_id"],
        "estado": carga["estado"],
        "columnas_archivo": carga["columnas"],
        "campos_destino": _resumir_catalogo(catalogo.get("modelos", {})),
    }


def confirmar_mapeo_columnas(carga_id: int, mapeos: list[dict], token: str | None = None) -> dict:
    """Guarda el mapeo de columnas que la persona confirmó en el chat: una
    lista de objetos con columna_origen, modelo_destino y campo_destino (y,
    si aplica, transformacion). Solo llamar esta tool después de que la
    persona haya aprobado explícitamente la propuesta."""
    if not token:
        return {"error": "No hay sesión de usuario para confirmar este mapeo."}

    return backend_client.confirmar_mapeo_ia(carga_id, mapeos, token=token)
