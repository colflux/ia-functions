from abc import ABC, abstractmethod

from app.domain.models import Message, ModelReply, ToolSpec


class ProveedorNoDisponible(Exception):
    """El proveedor no atendió la llamada por algo ajeno a la pregunta: cuota por
    minuto o por día agotada, sin crédito, caído o sin respuesta. Quien combina
    varios proveedores lo usa para pasar al siguiente. `espera` son los segundos
    que conviene no volver a intentarlo (0 si no se sabe)."""

    def __init__(self, mensaje: str, espera: float = 0) -> None:
        super().__init__(mensaje)
        self.espera = espera


class LLMProvider(ABC):
    @abstractmethod
    def converse(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        system: str | None = None,
    ) -> ModelReply:
        """Un turno de conversación. Si el modelo decide usar una o más
        tools, vienen en ModelReply.tool_calls; quien llama es responsable
        de ejecutarlas y de agregar el resultado como un nuevo Message con
        role="tool" antes de volver a llamar a converse(). Lanza
        ProveedorNoDisponible si el proveedor no pudo atender la llamada."""
        raise NotImplementedError
