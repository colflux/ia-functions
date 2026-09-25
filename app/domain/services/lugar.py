"""De dónde viene un archivo subido: el lugar que escribe la persona, verificado.

Formato que se pide (de lo general a lo particular al revés, como se escribe una
dirección):  «Ecosistema - vereda - municipio - departamento», por ejemplo
«Páramo de Guerrero - vereda Monquetiva - Guatavita - Cundinamarca», o unas
coordenadas «4.9136, -73.7363».

Se exige una de dos: coordenadas dentro de Colombia, o municipio y departamento.
Un archivo de datos con filas de varios sitios puede indicar «varios sitios»: su
ubicación está en las coordenadas de cada fila (solo se acepta para Excel y CSV).
El departamento se compara con la lista oficial; el municipio y la vereda, con
los que tienen sitios en la plataforma. Un municipio o una vereda que la
plataforma no conoce se acepta con aviso: puede ser un lugar nuevo de muestreo.
"""
import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

DEPARTAMENTOS = [
    "Amazonas", "Antioquia", "Arauca", "Atlántico", "Bogotá D.C.", "Bolívar", "Boyacá", "Caldas",
    "Caquetá", "Casanare", "Cauca", "Cesar", "Chocó", "Córdoba", "Cundinamarca", "Guainía",
    "Guaviare", "Huila", "La Guajira", "Magdalena", "Meta", "Nariño", "Norte de Santander",
    "Putumayo", "Quindío", "Risaralda", "San Andrés y Providencia", "Santander", "Sucre",
    "Tolima", "Valle del Cauca", "Vaupés", "Vichada",
]
ALIAS_DEPARTAMENTO = {"bogota": "Bogotá D.C.", "bogotadc": "Bogotá D.C.", "valle": "Valle del Cauca",
                      "guajira": "La Guajira", "sanandres": "San Andrés y Providencia"}
ECOSISTEMAS = ("paramo", "humedal", "turbera", "sabana", "morichal", "laguna", "cienaga", "pantano", "bosque")

# Colombia continental e insular, con margen
LATITUD = (-4.5, 13.6)
LONGITUD = (-82.0, -66.8)

EJEMPLO = ("Escríbelo así: «Ecosistema - vereda - municipio - departamento», por ejemplo "
           "«Páramo de Guerrero - vereda Monquetiva - Guatavita - Cundinamarca». "
           "También sirven coordenadas, por ejemplo «4.9136, -73.7363». Si es una tabla de datos "
           "de varios sitios con sus coordenadas, escribe «varios sitios».")
VARIOS = re.compile(r"^(varios|variossitios|variaszonas|variospuntos|variaslocalidades|variosparamos)$")


def _bonito(texto: str) -> str:
    """GUATAVITA → Guatavita; lo que ya viene con mayúsculas y minúsculas se deja."""
    return texto.title() if texto and texto.isupper() else texto


def normalizar(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    base = re.sub(r"\b(vereda|municipio|mpio|departamento|depto|dpto|de)\b\.?", " ", base)
    return re.sub(r"[^a-z0-9]", "", base)


@dataclass
class Lugar:
    texto: str
    ecosistema: str = ""
    vereda: str = ""
    municipio: str = ""
    departamento: str = ""
    latitud: float | None = None
    longitud: float | None = None
    sitio_cercano: dict[str, Any] | None = None
    avisos: list[str] = field(default_factory=list)
    varios: bool = False  # tabla con filas de varios sitios: cada fila trae su ubicación

    def resumen(self) -> str:
        if self.varios:
            return "varios sitios (según la ubicación de cada fila del archivo)"
        partes = [p for p in (self.ecosistema, self.vereda and f"vereda {self.vereda}",
                              self.municipio, self.departamento) if p]
        if self.latitud is not None:
            partes.append(f"coordenadas {self.latitud:.6f}, {self.longitud:.6f}")
        return " - ".join(partes) or self.texto

    def lineas(self) -> list[str]:
        lineas = [f"Lugar indicado por quien subió el archivo: {self.texto.strip()}"]
        if self.resumen() != self.texto.strip():
            lineas.append(f"Lugar interpretado: {self.resumen()}")
        if self.sitio_cercano:
            s = self.sitio_cercano
            lineas.append(f"Sitio de la plataforma más cercano: {s['etiqueta']} (a {s['distancia_km']} km)")
        return lineas


class LugarInvalido(ValueError):
    """El lugar no se pudo interpretar; el mensaje va dirigido a la persona."""


def coordenadas(texto: str) -> tuple[float, float] | None:
    """«4.9136, -73.7363», «4,9136 -73,7363» o «4.9136; -73.7363». Una longitud
    positiva dentro del rango de Colombia se toma como oeste (olvido del signo)."""
    numeros = re.findall(r"-?\d{1,3}[.,]\d+", texto)
    if len(numeros) < 2:
        return None
    lat, lon = (float(n.replace(",", ".")) for n in numeros[:2])
    if LONGITUD[0] <= -lon <= LONGITUD[1]:
        lon = -lon
    if not (LATITUD[0] <= lat <= LATITUD[1] and LONGITUD[0] <= lon <= LONGITUD[1]):
        raise LugarInvalido(f"Las coordenadas {lat}, {lon} no están en Colombia. {EJEMPLO}")
    return lat, lon


def distancia_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    cos_lat = math.cos(math.radians(lat1))
    return math.hypot((lat2 - lat1) * 111.0, (lon2 - lon1) * 111.0 * cos_lat)


def sitio_mas_cercano(lat: float, lon: float, sitios: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidatos = [s for s in sitios if s.get("latitud") is not None and s.get("longitud") is not None]
    if not candidatos:
        return None
    s = min(candidatos, key=lambda x: distancia_km(lat, lon, x["latitud"], x["longitud"]))
    etiqueta = s.get("nombre") or f"Sitio {s['id']}"
    lugar = ", ".join(_bonito(p) for p in (s.get("vereda"), s.get("municipio")) if p)
    return {"id": s["id"], "etiqueta": f"{etiqueta} ({lugar})" if lugar else etiqueta,
            "distancia_km": round(distancia_km(lat, lon, s["latitud"], s["longitud"]), 2),
            "latitud": s["latitud"], "longitud": s["longitud"]}


def interpretar(texto: str, sitios: list[dict[str, Any]]) -> Lugar:
    """Lugar verificado, o LugarInvalido con lo que falta. `sitios` son los de la
    plataforma, con vereda, municipio, departamento y coordenadas."""
    texto = (texto or "").strip()
    if len(texto) < 4:
        raise LugarInvalido(f"Necesito saber de dónde es el archivo. {EJEMPLO}")
    lugar = Lugar(texto=texto)
    if VARIOS.match(normalizar(texto)):
        lugar.varios = True
        return lugar

    coords = coordenadas(texto)
    resto = re.sub(r"-?\d{1,3}[.,]\d+", " ", texto) if coords else texto
    if coords:
        lugar.latitud, lugar.longitud = coords
        lugar.sitio_cercano = sitio_mas_cercano(*coords, sitios)

    municipios = {normalizar(s.get("municipio")): s.get("municipio") for s in sitios if s.get("municipio")}
    veredas = {normalizar(s.get("vereda")): s for s in sitios if s.get("vereda")}
    departamentos = {normalizar(d): d for d in DEPARTAMENTOS} | ALIAS_DEPARTAMENTO

    partes = [p.strip() for p in re.split(r"[-–—,;/|\n]", resto) if normalizar(p)]
    sin_asignar: list[str] = []
    for parte in partes:
        n = normalizar(parte)
        if not lugar.departamento and n in departamentos:
            lugar.departamento = departamentos[n]
        elif not lugar.municipio and n in municipios:
            lugar.municipio = _bonito(municipios[n])
        elif not lugar.vereda and n in veredas:
            lugar.vereda = _bonito(veredas[n]["vereda"])
        elif not lugar.ecosistema and any(n.startswith(e) for e in ECOSISTEMAS):
            lugar.ecosistema = parte
        else:
            sin_asignar.append(parte)

    # Lo que no se reconoció se asigna por posición, de derecha a izquierda:
    # lo más cercano al departamento es el municipio, luego la vereda.
    for parte in reversed(sin_asignar):
        if lugar.departamento and not lugar.municipio:
            lugar.municipio = parte
            lugar.avisos.append(f"El municipio «{parte}» no tiene sitios en la plataforma todavía; "
                                "revisa que esté bien escrito.")
        elif lugar.municipio and not lugar.vereda:
            lugar.vereda = re.sub(r"(?i)^vereda\s+", "", parte)
            lugar.avisos.append(f"La vereda «{lugar.vereda}» no está registrada en la plataforma; se guardará como la escribiste.")
        elif not lugar.ecosistema:
            lugar.ecosistema = parte

    if lugar.municipio and not lugar.departamento:
        # Un municipio conocido dice su departamento.
        dep = next((s.get("departamento") for s in sitios if normalizar(s.get("municipio")) == normalizar(lugar.municipio)), None)
        if dep:
            lugar.departamento = _bonito(dep)
    if lugar.vereda and lugar.municipio:
        registrada = veredas.get(normalizar(lugar.vereda))
        if registrada and registrada.get("municipio") and normalizar(registrada["municipio"]) != normalizar(lugar.municipio):
            lugar.avisos.append(f"En la plataforma, la vereda {lugar.vereda} está en {_bonito(registrada['municipio'])}, "
                                f"no en {lugar.municipio}; revisa el lugar.")

    if coords is None and not (lugar.municipio and lugar.departamento):
        falta = "el departamento" if lugar.municipio else "el municipio y el departamento"
        raise LugarInvalido(f"Me falta {falta}. {EJEMPLO}")
    return lugar
