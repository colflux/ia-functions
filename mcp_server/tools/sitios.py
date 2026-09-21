from mcp_server import backend_client


def listar_sitios(filtro: str | None = None, limite: int = 25) -> dict:
    """Lista los sitios de monitoreo con sus coordenadas, vía la API pública
    del backend (GET /api/geo/sitios/). Devuelve el total aparte del listado,
    para poder responder "cuántos hay" sin enviar todos los sitios."""
    sitios = backend_client.get_sitios(filtro)
    limite = max(1, min(int(limite or 25), 100))
    return {"total": len(sitios), "mostrados": min(len(sitios), limite), "sitios": sitios[:limite]}
