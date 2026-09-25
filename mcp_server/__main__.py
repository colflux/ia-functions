from mcp.server.fastmcp import FastMCP

from mcp_server.config import MCP_PORT
from mcp_server.tools.mediciones import consultar_promedio, consultar_ultima_medicion
from mcp_server.tools.datos import consultar_datos_campo
from mcp_server.tools.mediciones_gei import consultar_mediciones
from mcp_server.tools.sitios import listar_sitios

# Las mediciones dictadas en el chat no pasan por este servidor: las registra el
# asistente en su propia base (app/adapters/tools/mediciones_chat_tool_provider.py)
# hasta que el ETL de la plataforma pueda recibirlas.

app = FastMCP("colflux-backend", port=MCP_PORT)

app.add_tool(listar_sitios)
app.add_tool(consultar_mediciones)
app.add_tool(consultar_datos_campo)
app.add_tool(consultar_promedio)
app.add_tool(consultar_ultima_medicion)

if __name__ == "__main__":
    app.run(transport="streamable-http")
