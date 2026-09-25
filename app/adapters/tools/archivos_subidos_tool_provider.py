"""Consulta los archivos que las personas suben desde el chat.

La búsqueda por significado de buscar_documentos sirve para entrevistas y
textos, pero no encuentra una fila exacta de una tabla: una fecha o una
coordenada se parecen a cualquier otra. Esta herramienta tiene tres modos:
- con `terminos`, busca el texto tal cual y devuelve solo las filas que
  coinciden, con el encabezado de su tabla;
- con `archivo`, devuelve el contenido de ese archivo;
- sin nada, lista los archivos subidos con su descripción y un extracto.

Las fuentes que devuelve son claves del bucket: el chat ofrece con ellas la
descarga del original a reportadores y administradores."""

import re
from typing import Any

from app.domain.models import ToolSpec
from app.domain.ports.tool_provider import ToolProvider
from app.domain.ports.vector_store import VectorStore
from app.domain.services.carga_documentos import PREFIJO_DOCUMENTOS, nombre_visible

MAX_ARCHIVOS = 20
MAX_FRAGMENTOS = 30
MAX_FRAGMENTOS_ARCHIVO = 12
MAX_LINEAS_POR_ARCHIVO = 40
MAX_CARACTERES_EXTRACTO = 600
MAX_CARACTERES_CONTENIDO = 4000
MAX_CARACTERES_ADELANTO = 200
NOTA = ("Estos datos vienen de archivos subidos desde el chat: no han pasado por la "
        "validación del ETL de la plataforma. Dilo al citarlos.")
CABECERAS = ("Archivo:", "Descripción de quien subió el archivo:", "Datos aportados al subirlo:")


def _sin_tildes(texto: str) -> str:
    return texto.lower().translate(str.maketrans("áéíóúüñàèìòù", "aeiouunaeiou"))


def _normalizar_termino(termino: str) -> str:
    """4,676372 → 4.676372 (el archivo guarda los decimales con punto)."""
    return re.sub(r"(?<=\d),(?=\d)", ".", termino.strip())


def _descripcion(fragmento: str) -> str:
    for linea in fragmento.splitlines():
        if linea.startswith("Descripción de quien subió el archivo:"):
            return linea.split(":", 1)[1].strip()
    return ""


def _cuerpo(fragmento: str) -> str:
    """El texto del archivo, sin las líneas que se agregan al indexarlo."""
    return "\n".join(l for l in fragmento.splitlines() if not l.startswith(CABECERAS)).strip()


def _encabezado(fragmento: str) -> str | None:
    """Primera fila de la primera tabla (Excel o CSV extraídos con tabuladores)."""
    return next((linea for linea in fragmento.splitlines() if "\t" in linea), None)


def _ficha(fuente: str, primer_fragmento: str) -> dict[str, Any]:
    return {
        "archivo": nombre_visible(fuente),
        "tipo": fuente.split("/")[1],
        "descripcion": _descripcion(primer_fragmento),
    }


class ArchivosSubidosToolProvider(ToolProvider):
    def __init__(self, store: VectorStore, depurador=None) -> None:
        self._store = store
        # Retira del índice los archivos borrados del bucket antes de consultarlos.
        self._depurador = depurador

    def list_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="consultar_archivos_subidos",
                description=(
                    "Consulta los archivos que las personas subieron desde el chat: entrevistas, "
                    "términos, tablas de datos (flujos de CO2 y CH4, clima, suelo, biomasa) e "
                    "imágenes. Con 'archivo' devuelve el contenido de ese archivo; con 'terminos' "
                    "busca texto exacto (fecha, coordenada, sitio, gas, nombre) y devuelve las filas "
                    "o fragmentos que coinciden; sin nada, lista los archivos subidos. Úsala cuando "
                    "pregunten por algo que subieron o por un archivo concreto, cuando pidan "
                    "descargar un archivo subido, o cuando las mediciones de la plataforma no "
                    "tengan el dato."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "archivo": {
                            "type": "string",
                            "description": "Nombre del archivo, o parte de él, para leer su contenido.",
                        },
                        "terminos": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Textos que deben aparecer tal cual: fechas AAAA-MM-DD, coordenadas "
                                "con punto decimal, nombre del sitio, gas (CO2, CH4), nombre de una "
                                "persona."
                            ),
                        },
                    },
                },
            )
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name != "consultar_archivos_subidos":
            return {"error": f"Herramienta desconocida: {name}"}
        if self._depurador:
            self._depurador.depurar()
        archivo = str(arguments.get("archivo") or "").strip()
        if archivo:
            return self._leer(archivo)
        terminos = [_normalizar_termino(t) for t in arguments.get("terminos") or [] if str(t).strip()]
        if terminos:
            return self._buscar(terminos)
        # solo_lista: sin el comienzo de cada archivo. Lo usa la consulta que
        # encadena buscar_documentos, para no cargar texto que no se pidió.
        return self._listar(con_comienzo=not arguments.get("solo_lista"))

    def _listar(self, con_comienzo: bool = True) -> dict[str, Any]:
        primeros = self._store.primeros_fragmentos(PREFIJO_DOCUMENTOS, MAX_ARCHIVOS)
        if not primeros:
            return {"archivos": [], "mensaje": "Todavía no se ha subido ningún archivo desde el chat."}
        archivos = [_ficha(f.source, f.content) for f in primeros]
        if con_comienzo:
            for archivo, f in zip(archivos, primeros):
                archivo["comienzo"] = _cuerpo(f.content)[:MAX_CARACTERES_ADELANTO]
        sources = [{"source": f.source, "content": _descripcion(f.content), "score": 1.0} for f in primeros]
        return {
            "archivos": archivos,
            "nota": ("Estos son todos los archivos subidos desde el chat, con su contenido "
                     "completo: para leer uno, pasa su nombre en 'archivo'."),
            "sources": sources,
        }

    def _leer(self, archivo: str) -> dict[str, Any]:
        buscado = _sin_tildes(archivo)
        primeros = self._store.primeros_fragmentos(PREFIJO_DOCUMENTOS, 200)
        candidatos = [
            f for f in primeros
            if buscado in _sin_tildes(nombre_visible(f.source)) or buscado in _sin_tildes(_descripcion(f.content))
        ]
        if not candidatos:
            return {
                "error": f"Ningún archivo subido coincide con «{archivo}».",
                "archivos_disponibles": [nombre_visible(f.source) for f in primeros[:MAX_ARCHIVOS]],
            }
        # El mismo archivo subido más de una vez (con otra descripción) cuenta como
        # uno solo: se lee la versión más reciente, que es la primera de la lista.
        if len({nombre_visible(f.source) for f in candidatos}) > 1:
            return {
                "error": f"«{archivo}» coincide con varios archivos; indica cuál.",
                "candidatos": [_ficha(f.source, f.content) for f in candidatos[:MAX_ARCHIVOS]],
            }
        fuente, primero = candidatos[0].source, candidatos[0].content
        # Sin términos, buscar_texto trae todos los fragmentos de esa fuente (del
        # más reciente al más antiguo): se invierten para leer en orden.
        fragmentos = self._store.buscar_texto([], fuente, MAX_FRAGMENTOS_ARCHIVO)[::-1]
        contenido = "\n".join(_cuerpo(f.content) for f in fragmentos if f.source == fuente)
        recortado = len(contenido) > MAX_CARACTERES_CONTENIDO
        salida = {
            **_ficha(fuente, primero),
            "contenido": contenido[:MAX_CARACTERES_CONTENIDO],
            "sources": [{"source": fuente, "content": contenido[:MAX_CARACTERES_EXTRACTO], "score": 1.0}],
        }
        if recortado:
            salida["nota_contenido"] = ("El archivo es más largo: esto es el comienzo. Para algo "
                                        "concreto, busca con 'terminos'.")
        if fuente.split("/")[1] == "datos":
            salida["nota"] = NOTA
        return salida

    def _buscar(self, terminos: list[str]) -> dict[str, Any]:
        fragmentos = self._store.buscar_texto(terminos, PREFIJO_DOCUMENTOS, MAX_FRAGMENTOS)
        if not fragmentos:
            return {
                "archivos": [],
                "mensaje": ("Ningún archivo subido contiene todos esos términos. Prueba con menos "
                            "términos, o sin términos para ver qué archivos hay."),
            }
        por_fuente: dict[str, list[str]] = {}
        for f in fragmentos:
            por_fuente.setdefault(f.source, []).append(f.content)
        primeros = {f.source: f.content for f in self._store.primeros_fragmentos(
            PREFIJO_DOCUMENTOS, len(por_fuente), list(por_fuente))}

        archivos, sources = [], []
        buscados = [_sin_tildes(t) for t in terminos]
        for fuente, contenidos in por_fuente.items():
            lineas = [
                linea for c in contenidos for linea in c.splitlines()
                if all(t in _sin_tildes(linea) for t in buscados)
            ]
            lineas = list(dict.fromkeys(lineas))  # la misma fila puede venir de dos fragmentos
            entrada = _ficha(fuente, primeros.get(fuente, ""))
            if lineas:  # tabla: las filas que coinciden, con su encabezado
                entrada["encabezado"] = _encabezado(primeros.get(fuente, ""))
                entrada["filas"] = lineas[:MAX_LINEAS_POR_ARCHIVO]
                entrada["filas_encontradas"] = len(lineas)
                extracto = "\n".join(lineas[:5])
            else:  # texto: los términos aparecen en el fragmento, no en una sola línea
                extracto = contenidos[0][:MAX_CARACTERES_EXTRACTO]
                entrada["fragmento"] = extracto
            archivos.append(entrada)
            sources.append({"source": fuente, "content": extracto, "score": 1.0})
        return {"archivos": archivos, "nota": NOTA, "sources": sources}
