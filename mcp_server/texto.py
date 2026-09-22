"""Normalización de texto para comparar nombres escritos por personas."""

import unicodedata


def normalizar(texto) -> str:
    """Minúsculas, sin tildes, sin espacios ni signos.

    Los sitios se llaman SanAntonioParamos o ChocolatalBInt, pero nadie los
    escribe así. Normalizando ambos lados, san antonio paramos encuentra
    SanAntonioParamos."""
    base = unicodedata.normalize("NFKD", str(texto or "").lower())
    return "".join(c for c in base if c.isalnum())
