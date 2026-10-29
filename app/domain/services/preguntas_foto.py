"""Preguntas que el bot hace, una a una, sobre cada foto de un grupo de observación.

Salen del «Instrumento de preguntas» del proyecto (versión 18-08-2026): todas
sus preguntas, en su orden. Las de la categoría 0 (caracterización individual)
son de la persona, no de la foto: se hacen una sola vez, la primera vez que
describe una foto, y se pueden saltar con «Prefiero no responder». Viven en un
listado que el equipo edita sin tocar código: app/data/preguntas_foto.csv (o la
ruta de la variable PREGUNTAS_FOTO), separado por punto y coma, con estas columnas:

  codigo                    identificador fijo (C3-1); va en cada respuesta y en el Excel
  orden                     en qué orden se preguntan
  alcance                   persona (una sola vez por persona) o foto (en cada foto); vacío = foto
  categoria                 categoría del instrumento (Agua, Suelo...)
  pregunta                  el texto que ve la persona
  origen_en_el_instrumento  de qué pregunta del instrumento sale
  activa                    si / no

Se lee de nuevo en cada uso, así que un cambio en el archivo vale sin reiniciar.
La pregunta que toca es la primera del listado que la persona aún no ha
respondido sobre esa foto; al final viene la pregunta abierta (ADICIONAL).
Cada respuesta guarda el código y el texto de su pregunta, así que editar una
pregunta no cambia lo ya respondido.
"""
import csv
import logging
import os
import unicodedata
from pathlib import Path
from typing import Any

logger = logging.getLogger("uvicorn.error")

RUTA = Path(os.environ.get("PREGUNTAS_FOTO") or Path(__file__).resolve().parents[2] / "data" / "preguntas_foto.csv")
INSTRUMENTO = "Instrumento de preguntas, versión 18-08-2026 (redacción adaptada a la foto)"
CIERRE = "ADICIONAL"
TEXTO_CIERRE = "¿Observas algo más en la imagen que no hayas descrito?"
_NO_SABE = {"no se", "no lo se", "ni idea", "no sabria", "no sabria decir", "no se decir"}
_NO_APLICA = {"no aplica", "no aplica a esta foto", "no corresponde", "no corresponde a la imagen",
              "no corresponde a la foto"}
NO_RESPONDE = "Prefiero no responder"
_NO_RESPONDE = {"prefiero no responder", "no quiero responder", "prefiero no decir", "prefiero no decirlo",
                "no responder"}
PERSONA, FOTO = "persona", "foto"


def _plano(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    return " ".join("".join(c if c.isalnum() or c == " " else " " for c in base).split())


def respuesta_especial(texto: str) -> str | None:
    """«No sé» → "no_sabe"; «No aplica a esta foto» → "no_aplica"; «Prefiero no
    responder» → "no_responde"; lo demás → None."""
    plano = _plano(texto)
    if plano in _NO_RESPONDE:
        return "no_responde"
    if plano in _NO_SABE:
        return "no_sabe"
    if plano in _NO_APLICA:
        return "no_aplica"
    return None


def _filas() -> list[dict[str, str]]:
    try:
        contenido = RUTA.read_text(encoding="utf-8-sig")
    except OSError:
        logger.warning("PREGUNTAS no se encontró %s; se describe sin preguntas", RUTA)
        return []
    lineas = contenido.splitlines()
    if not lineas:
        return []
    separador = ";" if lineas[0].count(";") >= lineas[0].count(",") else ","
    return [{(k or "").strip(): (v or "").strip() for k, v in fila.items()}
            for fila in csv.DictReader(lineas, delimiter=separador)]


def todas() -> list[dict[str, str]]:
    """Todas las filas del listado, activas o no (para la hoja «Preguntas» del Excel)."""
    return _filas()


def cargar() -> list[dict[str, Any]]:
    """Las preguntas activas, en orden. Si el archivo falta, lista vacía: la
    descripción sigue como antes, solo con la pregunta abierta del final."""
    preguntas = []
    for fila in _filas():
        if not fila.get("codigo") or not fila.get("pregunta"):
            continue
        if _plano(fila.get("activa", "si")) in ("no", "n", "false", "0"):
            continue
        try:
            orden = float(fila.get("orden") or 0)
        except ValueError:
            orden = 0
        alcance = PERSONA if _plano(fila.get("alcance", "")) == PERSONA else FOTO
        preguntas.append({"codigo": fila["codigo"].upper(), "orden": orden, "categoria": fila.get("categoria", ""),
                          "pregunta": fila["pregunta"], "origen": fila.get("origen_en_el_instrumento", ""),
                          "alcance": alcance})
    # Primero las de la persona, después las de la foto, cada grupo en su orden.
    return sorted(preguntas, key=lambda p: (p["alcance"] != PERSONA, p["orden"]))


def de_persona(preguntas: list[dict[str, Any]]) -> set[str]:
    return {p["codigo"] for p in preguntas if p["alcance"] == PERSONA}


def paso(preguntas: list[dict[str, Any]], numero: int) -> dict[str, Any]:
    """Lo que toca en el paso `numero`: una pregunta o, al final, la pregunta abierta."""
    total = len(preguntas)
    if numero < total:
        p = preguntas[numero]
        grupo = [q for q in preguntas if q["alcance"] == p["alcance"]]
        # numero_en/de: el lugar dentro de su grupo («Sobre usted · 3 de 10», «Pregunta 5 de 31»).
        return {"paso": numero, "total": total, "codigo": p["codigo"], "categoria": p["categoria"],
                "pregunta": p["pregunta"], "cierre": False, "alcance": p["alcance"],
                "numero_en": grupo.index(p) + 1, "de": len(grupo)}
    return {"paso": numero, "total": total, "codigo": CIERRE, "categoria": "", "pregunta": TEXTO_CIERRE,
            "cierre": True, "alcance": FOTO, "numero_en": 0, "de": 0}


def siguiente(preguntas: list[dict[str, Any]], respondidas: set[str]) -> dict[str, Any]:
    """La primera pregunta que la persona aún no respondió sobre esa foto (o la
    pregunta abierta del final). Así, si deja la foto a medias y vuelve a
    abrirla, sigue donde iba; y un cambio en el listado no desordena nada."""
    for numero, p in enumerate(preguntas):
        if p["codigo"] not in respondidas:
            return paso(preguntas, numero)
    return paso(preguntas, len(preguntas))


def alcance_de(codigo: str | None, preguntas: list[dict[str, Any]] | None = None) -> str:
    preguntas = cargar() if preguntas is None else preguntas
    return next((p["alcance"] for p in preguntas if p["codigo"] == codigo), FOTO)


def categoria_de(codigo: str | None) -> str:
    if codigo == CIERRE:
        return "Pregunta abierta"
    return next((p["categoria"] for p in cargar() if p["codigo"] == codigo), "")


def texto_de(codigo: str | None) -> str:
    """El texto de una pregunta por su código (la pregunta abierta del final, o vacío)."""
    if not codigo:
        return ""
    if codigo == CIERRE:
        return TEXTO_CIERRE
    return next((p["pregunta"] for p in cargar() if p["codigo"] == codigo), "")
