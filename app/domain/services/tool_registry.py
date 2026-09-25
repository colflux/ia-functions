from typing import Any

from app.domain.models import ToolSpec
from app.domain.ports.tool_provider import ToolProvider


class ToolRegistry:
    """Agrega varios ToolProvider (locales y remotos) en un único catálogo
    que el orquestador consulta sin saber de dónde viene cada tool."""

    def __init__(self, providers: list[ToolProvider]) -> None:
        self._providers = providers
        self._owner_by_tool: dict[str, ToolProvider] = {}
        self._specs: list[ToolSpec] = []
        for provider in providers:
            for spec in provider.list_tools():
                self._owner_by_tool[spec.name] = provider
                self._specs.append(spec)
            # Herramientas que solo ejecuta el orquestador (por ejemplo guardar tras
            # «confirmo»): se pueden llamar, pero no se le ofrecen al modelo.
            for nombre in getattr(provider, "ocultas", []):
                self._owner_by_tool[nombre] = provider

    def list_tools(self) -> list[ToolSpec]:
        return self._specs

    def tiene(self, name: str) -> bool:
        return name in self._owner_by_tool

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        provider = self._owner_by_tool.get(name)
        if provider is None:
            return {"error": f"Herramienta desconocida: {name}"}
        try:
            return provider.call_tool(name, arguments)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
