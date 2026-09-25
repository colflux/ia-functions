"""Excel con los datos que la persona consultó en el chat.

Cuando una herramienta de datos se llama con exportar=true, devuelve todas las
filas (no solo la muestra que ve el modelo). El orquestador las junta: cada
consulta es una hoja del mismo libro. El archivo se guarda en el bucket bajo
descargas/ y el chat muestra un botón que pide un enlace temporal. Los datos ya
son públicos en el geoportal, así que cualquiera puede descargarlo.
"""
import io
import logging
import re
import uuid
from datetime import date
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from app.domain.ports.file_storage import FileStorage

logger = logging.getLogger("uvicorn.error")

PREFIJO = "descargas/"
SEGUNDOS_ENLACE = 3600
MAX_FILAS_HOJA = 100_000
TIPO_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _nombre_hoja(titulo: str, usados: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", " ", titulo or "Datos").strip()[:28] or "Datos"
    nombre, n = base, 2
    while nombre.lower() in usados:
        nombre, n = f"{base[:26]} {n}", n + 1
    usados.add(nombre.lower())
    return nombre


class Descargas:
    def __init__(self, almacen: FileStorage | None) -> None:
        self._almacen = almacen

    @property
    def disponible(self) -> bool:
        return self._almacen is not None

    def guardar(self, hojas: list[dict[str, Any]]) -> dict[str, Any] | None:
        """hojas: [{"titulo": str, "filas": [dict, ...]}]. Devuelve {archivo, nombre, filas, hojas}."""
        if self._almacen is None or not hojas:
            return None
        libro = Workbook()
        libro.remove(libro.active)
        usados: set[str] = set()
        total = 0
        for hoja in hojas:
            filas = (hoja.get("filas") or [])[:MAX_FILAS_HOJA]
            ws = libro.create_sheet(_nombre_hoja(hoja.get("titulo", "Datos"), usados))
            columnas: list[str] = []
            for f in filas:
                columnas.extend(k for k in f if k not in columnas)
            ws.append(columnas or ["sin datos"])
            for celda in ws[1]:
                celda.font = Font(bold=True)
            for f in filas:
                ws.append([f.get(c) if not isinstance(f.get(c), (dict, list)) else str(f.get(c)) for c in columnas])
            ws.freeze_panes = "A2"
            for i, c in enumerate(columnas, 1):
                ancho = max([len(str(c))] + [len(str(f.get(c) or "")) for f in filas[:200]])
                ws.column_dimensions[get_column_letter(i)].width = min(max(ancho + 2, 10), 45)
            total += len(filas)
        contenido = io.BytesIO()
        libro.save(contenido)
        clave = f"{PREFIJO}{date.today():%Y-%m-%d}-{uuid.uuid4().hex[:8]}.xlsx"
        self._almacen.subir(clave, contenido.getvalue(), TIPO_XLSX, {"filas": str(total)})
        logger.info("EXCEL %s con %d hojas y %d filas", clave, len(hojas), total)
        return {"archivo": clave, "nombre": f"colflux-datos-{date.today():%Y-%m-%d}.xlsx",
                "filas": total, "hojas": [h.get("titulo") for h in hojas]}

    def enlace(self, clave: str) -> str | None:
        if self._almacen is None or not clave.startswith(PREFIJO) or ".." in clave or not clave.endswith(".xlsx"):
            return None
        return self._almacen.enlace_descarga(clave, "colflux-" + clave[len(PREFIJO):], SEGUNDOS_ENLACE)
