"""Reglas cuantitativas del diccionario de campo aplicadas a un dato medido.

El diccionario (v2) acompaña cada expresión de campo con una regla, por ejemplo
«Olor a huevo podrido: MUY ALTO si Flujo de CH4 ≥ 0.0735 µmol/m²/s (≥ 73.5
nmol/m²/s)». Cuando una herramienta devuelve un valor de CO2 o CH4 que cumple
una de esas reglas, el asistente puede decir en una frase que, según el
diccionario, en ese sitio es posible observar eso.

Solo se usan las reglas simples de un gas contra un umbral. Las que combinan la
condición de luz u otro gas se dejan fuera: la herramienta no siempre trae esos
datos juntos y una relación dudosa es peor que ninguna. Se compara siempre en
la unidad en que viene el dato, nunca convirtiendo.
"""
import logging
import re
import time
from dataclasses import dataclass

from app.domain.ports.vector_store import VectorStore

logger = logging.getLogger("uvicorn.error")

PREFIJO_DICCIONARIO = "diccionario-"
SEGUNDOS_CACHE = 600

REGLA_SIMPLE = re.compile(
    r"^(?:(?P<nivel>[A-ZÁÉÍÓÚÑ ]+?) si |Asociada a flujos muy altos: )"
    r"Flujo de (?P<gas>CO2|CH4) ≥ (?P<umol>-?[\d.]+) µmol/m²/s "
    r"\(≥ (?P<otro>-?[\d.]+) (?P<otra_unidad>nmol/m²/s|g/m²/h)\)"
)


@dataclass
class Regla:
    termino: str
    fuente: str
    contenido: str
    gas: str
    nivel: str
    umbrales: dict[str, float]  # unidad normalizada → umbral


def unidad_normalizada(unidad: str | None) -> str | None:
    u = (unidad or "").lower()
    if "nmol" in u:
        return "nmol/m²/s"
    if "umol" in u or "µmol" in u or "micromol" in u:
        return "µmol/m²/s"
    if "g_m2_h" in u or u.startswith("g/") or u.startswith("g m"):
        return "g/m²/h"
    return None


class ReglasDiccionario:
    def __init__(self, vector_store: VectorStore) -> None:
        self._vector_store = vector_store
        self._cache: tuple[float, list[Regla]] | None = None

    def evaluar(self, gas: str, unidad: str | None, valor: float | None) -> Regla | None:
        """La regla más exigente que ese valor cumple, o None. Ante la duda, None."""
        u = unidad_normalizada(unidad)
        if valor is None or u is None:
            return None
        cumplidas = [r for r in self._reglas()
                     if r.gas == gas.upper() and u in r.umbrales and valor >= r.umbrales[u]]
        # La más alta; a igualdad, la primera del diccionario.
        return max(cumplidas, key=lambda r: r.umbrales[u], default=None)

    def _reglas(self) -> list[Regla]:
        if self._cache and time.monotonic() - self._cache[0] < SEGUNDOS_CACHE:
            return self._cache[1]
        try:
            fragmentos = self._vector_store.buscar_texto(["Regla cuantitativa"], PREFIJO_DICCIONARIO, 500)
        except Exception:
            logger.exception("REGLAS no se pudo leer el diccionario")
            return self._cache[1] if self._cache else []
        reglas = []
        for f in sorted(fragmentos, key=lambda x: x.source):
            m = re.search(r"Regla cuantitativa:\s*(.+?)(?:\.\s+Interpretación:|$)", f.content, re.DOTALL)
            texto = m.group(1).strip() if m else ""
            if not texto or "Condición" in texto or " Y (" in texto or " O (" in texto:
                continue
            simple = REGLA_SIMPLE.search(texto)
            if not simple:
                continue
            reglas.append(Regla(
                termino=re.sub(rf"^{PREFIJO_DICCIONARIO}\d+\s*", "", f.source),
                fuente=f.source,
                contenido=f.content,
                gas=simple["gas"],
                nivel=(simple["nivel"] or "MUY ALTO").strip(),
                umbrales={"µmol/m²/s": float(simple["umol"]), simple["otra_unidad"]: float(simple["otro"])},
            ))
        logger.info("REGLAS %d reglas simples cargadas del diccionario", len(reglas))
        self._cache = (time.monotonic(), reglas)
        return reglas
