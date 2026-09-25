"""Revisión automática de archivos de datos grandes (Excel o CSV).

Un libro con miles de filas no cabe como texto en la base de conocimiento, y
tampoco serviría: el asistente vería solo fragmentos. En su lugar se arma un
perfil de cada hoja (filas, columnas, vacíos, rangos, valores) y una lista de
hallazgos de calidad que conviene corregir antes de pasar los datos por el ETL.
El perfil es lo que se indexa; el original queda en el bucket.

Los hallazgos son comprobaciones simples y explicables; cada uno dice cuántas
filas afecta. No se corrige nada: solo se informa.
"""
import csv
import io
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path

import openpyxl

TABULARES = {".xlsx", ".xlsm", ".csv"}
MAX_VALORES_LISTADOS = 6
MAX_CARACTERES_HOJA_COMPLETA = 20_000  # hojas pequeñas (diccionarios, catálogos) se incluyen enteras
MAX_EJEMPLOS = 3
LAT_COLOMBIA = (-4.3, 13.5)
LON_COLOMBIA = (-79.1, -66.8)
HORAS_DIA = range(7, 18)
HORAS_NOCHE = set(range(0, 5)) | set(range(20, 24))


@dataclass
class Hoja:
    nombre: str
    columnas: list[str]
    filas: list[tuple]
    resumen: list[str] = field(default_factory=list)
    graves: list[str] = field(default_factory=list)     # impiden usar filas: van primero
    hallazgos: list[str] = field(default_factory=list)  # conviene revisarlos
    caracteres: int = 0


@dataclass
class Perfil:
    archivo: str
    hojas: list[Hoja]

    @property
    def caracteres(self) -> int:
        return sum(h.caracteres for h in self.hojas)

    @property
    def total_filas(self) -> int:
        return sum(len(h.filas) for h in self.hojas)

    @property
    def hallazgos(self) -> list[str]:
        """Primero los graves; un mismo hallazgo en varias hojas va en una línea."""
        hojas_por_texto: dict[str, list[str]] = {}
        for grupo in ("graves", "hallazgos"):
            for h in self.hojas:
                for x in getattr(h, grupo):
                    hojas_por_texto.setdefault(x, []).append(h.nombre)
        return [f"{', '.join(hojas)}: {x}" for x, hojas in hojas_por_texto.items()]

    def texto(self) -> str:
        """Lo que se indexa: perfil de cada hoja, hallazgos y hojas pequeñas completas."""
        partes = [f"Revisión automática del archivo de datos «{self.archivo}»: {len(self.hojas)} hojas, "
                  f"{self.total_filas} filas. Son datos subidos para revisión: todavía no son mediciones "
                  "de la plataforma (deben pasar por el ETL)."]
        for h in self.hojas:
            partes.append(f"\nHoja «{h.nombre}»: {len(h.filas)} filas, {len(h.columnas)} columnas.")
            partes.extend(f"- {r}" for r in h.resumen)
            if h.graves or h.hallazgos:
                partes.append(f"Hallazgos de la hoja «{h.nombre}»:")
                partes.extend(f"- {x}" for x in h.graves + h.hallazgos)
            if h.caracteres <= MAX_CARACTERES_HOJA_COMPLETA:
                partes.append(f"Contenido completo de la hoja «{h.nombre}»:")
                partes.append(" | ".join(h.columnas))
                partes.extend(" | ".join(_texto(v) for v in fila) for fila in h.filas)
        return "\n".join(partes)


def es_tabular(nombre: str) -> bool:
    return Path(nombre).suffix.lower() in TABULARES


def perfilar(nombre: str, contenido: bytes) -> Perfil:
    hojas = _leer_hojas(nombre, contenido)
    instalacion = _fechas_de_instalacion(hojas)
    for hoja in hojas:
        _describir(hoja)
        _revisar(hoja, instalacion)
    return Perfil(nombre, hojas)


# --- lectura -----------------------------------------------------------------

def _leer_hojas(nombre: str, contenido: bytes) -> list[Hoja]:
    if Path(nombre).suffix.lower() == ".csv":
        texto = contenido.decode("utf-8-sig", "replace")
        try:
            dialecto = csv.Sniffer().sniff(texto[:5000], delimiters=",;\t|")
        except csv.Error:
            dialecto = csv.excel
        tablas = [(Path(nombre).stem, list(csv.reader(io.StringIO(texto), dialecto)))]
    else:
        libro = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
        tablas = [(ws.title, list(ws.iter_rows(values_only=True))) for ws in libro.worksheets]
    hojas = []
    for titulo, filas in tablas:
        filas = [tuple(f) for f in filas if any(_vacio(v) is False for v in f)]
        if not filas:
            continue
        # La cabecera es la primera fila con al menos dos celdas escritas.
        inicio = next((i for i, f in enumerate(filas) if sum(not _vacio(v) for v in f) >= 2), 0)
        cabecera = filas[inicio]
        ancho = max(i for i, v in enumerate(cabecera) if not _vacio(v)) + 1
        datos = [list(f[:ancho]) + [None] * (ancho - len(f)) for f in filas[inicio + 1:]]
        # Columnas sin título ni datos (márgenes del Excel) no son columnas.
        usadas = [i for i in range(ancho) if not _vacio(cabecera[i]) or any(not _vacio(f[i]) for f in datos)]
        columnas = [_texto(cabecera[i]) or f"columna {i + 1}" for i in usadas]
        datos = [tuple(f[i] for i in usadas) for f in datos]
        hoja = Hoja(titulo, columnas, datos)
        hoja.caracteres = sum(len(" | ".join(_texto(v) for v in f)) for f in [cabecera] + datos)
        hojas.append(hoja)
    return hojas


# --- descripción ---------------------------------------------------------------

def _describir(hoja: Hoja) -> None:
    for i, columna in enumerate(hoja.columnas):
        valores = [f[i] for f in hoja.filas]
        llenos = [v for v in valores if not _vacio(v)]
        vacios = len(valores) - len(llenos)
        linea = f"«{columna}»: {len(llenos)} con dato" + (f", {vacios} vacías" if vacios else "")
        fechas = [d for d in (_fecha(v) for v in llenos) if d]
        numeros = [n for n in (_numero(v) for v in llenos) if n is not None]
        distintos = Counter(_texto(v) for v in llenos)
        if fechas and len(fechas) >= len(llenos) * 0.9:
            linea += f"; fechas del {min(fechas):%d/%m/%Y} al {max(fechas):%d/%m/%Y}"
        elif numeros and len(numeros) >= len(llenos) * 0.9 and len(distintos) > MAX_VALORES_LISTADOS:
            linea += f"; números de {_g(min(numeros))} a {_g(max(numeros))}, mediana {_g(statistics.median(numeros))}"
        elif distintos and len(distintos) <= MAX_VALORES_LISTADOS:
            linea += "; valores: " + ", ".join(f"{v} ({n})" for v, n in distintos.most_common())
        elif distintos:
            ejemplos = ", ".join(v for v, _ in distintos.most_common(MAX_EJEMPLOS))
            linea += f"; {len(distintos)} valores distintos, p. ej. {ejemplos}"
        hoja.resumen.append(linea)


# --- comprobaciones ------------------------------------------------------------

def _revisar(hoja: Hoja, instalacion: dict[str, date]) -> None:
    cols = [_normal(c) for c in hoja.columnas]
    g, x = hoja.graves, hoja.hallazgos

    repetidas = [c for c, n in Counter(hoja.columnas).items() if n > 1]
    if repetidas:
        g.append("columnas con el mismo nombre: " + ", ".join(f"«{c}» ({Counter(hoja.columnas)[c]} veces)" for c in repetidas)
                 + "; no se sabe cuál es cuál")

    for i, c in enumerate(hoja.columnas):
        vacias = sum(_vacio(f[i]) for f in hoja.filas)
        if hoja.filas and vacias == len(hoja.filas):
            x.append(f"la columna «{c}» está vacía en todas las filas")

    duplicadas = len(hoja.filas) - len(set(hoja.filas))
    if duplicadas:
        x.append(f"{duplicadas} filas repetidas exactamente")

    # Flujos de gases: sin columna de valor, valores vacíos, unidades mezcladas o vacías.
    i_gas = _columna(cols, "gas")
    i_valor = _columna(cols, "valor", "flujo") if i_gas is not None else None
    if i_valor is not None and "unidad" in cols[i_valor]:
        i_valor = None
    i_unidad = _columna(cols, "unidad del flujo", "unidad")
    if i_gas is not None and i_unidad is None:
        g.append("tiene gas pero ninguna columna con la unidad del flujo")
    if i_gas is not None and _columna(cols, "condicion de luz", "luz") is None:
        x.append("tiene gas pero ninguna columna con la condición de luz (día o noche)")
    if i_gas is not None and i_valor is None:
        g.append(f"tiene gas y unidad pero ninguna columna con el valor del flujo: sus {len(hoja.filas)} filas no traen medición")
    if i_valor is not None:
        sin_valor = [f for f in hoja.filas if _numero(f[i_valor]) is None]
        if sin_valor:
            g.append(f"{len(sin_valor)} filas sin valor en «{hoja.columnas[i_valor]}»" + _por_columna(hoja, sin_valor, cols, "analizador", "equipo"))
        _atipicos(hoja, i_valor, x)
    if i_unidad is not None and i_gas is not None:
        unidades = Counter(_texto(f[i_unidad]) for f in hoja.filas if not _vacio(f[i_unidad]))
        if len(unidades) > 1:
            x.append("mezcla unidades: " + ", ".join(f"{u} ({n})" for u, n in unidades.most_common())
                     + "; no se pueden comparar ni promediar juntas")
        sin_unidad = sum(_vacio(f[i_unidad]) for f in hoja.filas)
        if sin_unidad:
            g.append(f"{sin_unidad} filas sin unidad")

    # Condición de luz contra la hora de la toma.
    i_luz, i_hora = _columna(cols, "condicion de luz", "luz"), _columna(cols, "hora")
    if i_luz is not None and i_hora is not None:
        noche_de_dia = dia_de_noche = 0
        for f in hoja.filas:
            hora, luz = _hora(f[i_hora]), _normal(_texto(f[i_luz]))
            if hora is None:
                continue
            noche_de_dia += luz == "noche" and hora in HORAS_DIA
            dia_de_noche += luz == "dia" and hora in HORAS_NOCHE
        if noche_de_dia:
            x.append(f"{noche_de_dia} filas marcadas «Noche» tomadas entre las 7:00 y las 17:59")
        if dia_de_noche:
            x.append(f"{dia_de_noche} filas marcadas «Día» tomadas entre las 20:00 y las 4:59")

    # Porcentajes imposibles.
    for i, c in enumerate(cols):
        if "%" in hoja.columnas[i] or "porcentaje" in c:
            fuera = [n for n in (_numero(f[i]) for f in hoja.filas) if n is not None and not 0 <= n <= 100]
            if fuera:
                g.append(f"{len(fuera)} valores de «{hoja.columnas[i]}» fuera de 0–100 (hasta {_g(max(fuera, key=abs))})")

    # Coordenadas fuera de Colombia.
    for nombre, rango in (("latitud", LAT_COLOMBIA), ("longitud", LON_COLOMBIA)):
        i = _columna(cols, nombre)
        if i is not None:
            fuera = sum(1 for f in hoja.filas if (n := _numero(f[i])) is not None and not rango[0] <= n <= rango[1])
            if fuera:
                g.append(f"{fuera} filas con {nombre} fuera de Colombia")

    # Fechas: vacías, futuras y anteriores a la instalación de su unidad de muestreo.
    i_fecha = next((i for i, c in enumerate(cols) if c.startswith("fecha") and "instalacion" not in c), None)
    if i_fecha is not None:
        fechas = [_fecha(f[i_fecha]) for f in hoja.filas]
        if sin := sum(d is None for d in fechas):
            x.append(f"{sin} filas sin «{hoja.columnas[i_fecha]}»")
        if futuras := sum(1 for d in fechas if d and d > date.today()):
            g.append(f"{futuras} filas con fecha futura")
        i_inst = next((i for i, c in enumerate(cols) if "instalacion" in c), None)
        i_um = _columna(cols, "nombre unidad de muestreo", "unidad de muestreo")
        antes = 0
        for f, d in zip(hoja.filas, fechas):
            inst = _fecha(f[i_inst]) if i_inst is not None else None
            if inst is None and i_um is not None:
                inst = instalacion.get(_texto(f[i_um]))
            antes += bool(d and inst and d < inst)
        if antes:
            x.append(f"{antes} filas con «{hoja.columnas[i_fecha]}» anterior a la fecha de instalación de su unidad de muestreo")

    # Nombres que solo cambian en mayúsculas, tildes o espacios.
    for i, c in enumerate(cols):
        if "sitio" in c or "unidad experimental" in c:
            grupos = defaultdict(set)
            for f in hoja.filas:
                if not _vacio(f[i]):
                    grupos[re.sub(r"\s+", "", _normal(str(f[i])))].add(str(f[i]))
            for formas in (sorted(v) for v in grupos.values() if len(v) > 1):
                x.append(f"«{hoja.columnas[i]}» escribe el mismo nombre de varias formas: "
                         + ", ".join(f"«{n}»" if n == n.strip() else f"«{n.strip()}» con espacios" for n in formas))


def _atipicos(hoja: Hoja, i: int, x: list[str]) -> None:
    """Valores muy alejados del resto (más de 3 rangos intercuartílicos)."""
    numeros = sorted(n for f in hoja.filas if (n := _numero(f[i])) is not None)
    if len(numeros) < 20:
        return
    q1, _, q3 = statistics.quantiles(numeros, n=4)
    rango = q3 - q1
    if rango <= 0:
        return
    fuera = [n for n in numeros if n < q1 - 3 * rango or n > q3 + 3 * rango]
    if fuera:
        extremos = sorted(set(fuera), key=abs, reverse=True)[:MAX_EJEMPLOS]
        x.append(f"{len(fuera)} valores de «{hoja.columnas[i]}» muy alejados del resto (p. ej. "
                 + ", ".join(_g(n) for n in extremos) + "); conviene confirmarlos")


def _fechas_de_instalacion(hojas: list[Hoja]) -> dict[str, date]:
    """Unidad de muestreo → fecha de instalación, tomada de cualquier hoja que
    traiga ambas columnas (normalmente la de unidades de muestreo)."""
    fechas: dict[str, date] = {}
    for h in hojas:
        cols = [_normal(c) for c in h.columnas]
        i_um = _columna(cols, "nombre unidad de muestreo")
        i_inst = next((i for i, c in enumerate(cols) if "instalacion" in c), None)
        if i_um is None or i_inst is None:
            continue
        for f in h.filas:
            if (d := _fecha(f[i_inst])) and not _vacio(f[i_um]):
                fechas.setdefault(_texto(f[i_um]), d)
    return fechas


def _por_columna(hoja: Hoja, filas: list[tuple], cols: list[str], *nombres: str) -> str:
    i = _columna(cols, *nombres)
    if i is None:
        return ""
    conteo = Counter(_texto(f[i]) or "sin dato" for f in filas)
    return " (" + ", ".join(f"{v}: {n}" for v, n in conteo.most_common(MAX_VALORES_LISTADOS)) + ")"


# --- utilidades ----------------------------------------------------------------

def _columna(cols: list[str], *nombres: str) -> int | None:
    """Primera columna igual a alguno de los nombres; si no hay, la primera que lo contenga."""
    for n in nombres:
        if n in cols:
            return cols.index(n)
    for n in nombres:
        for i, c in enumerate(cols):
            if n in c:
                return i
    return None


def _normal(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sin_tildes.lower().strip()


def _vacio(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _texto(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return f"{v:%Y-%m-%d}" if v.time() == time(0) else f"{v:%Y-%m-%d %H:%M}"
    if isinstance(v, float):
        return _g(v)
    return str(v).strip()


def _numero(v) -> float | None:
    if isinstance(v, bool) or _vacio(v):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip().replace(",", "."))
    except ValueError:
        return None


def _fecha(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        for formato in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(v.strip()[:10], formato).date()
            except ValueError:
                continue
    return None


def _hora(v) -> int | None:
    if isinstance(v, (time, datetime)):
        return v.hour
    m = re.match(r"\s*(\d{1,2}):\d{2}", str(v or ""))
    return int(m.group(1)) if m and int(m.group(1)) < 24 else None


def _g(n: float) -> str:
    return f"{n:.6g}"
