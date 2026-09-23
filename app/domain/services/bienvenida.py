"""Saludos y presentación del asistente: texto fijo, sin llamar al modelo.

Vive aparte del orquestador a propósito. Ajustar una palabra clave o reescribir
la presentación es un cambio de contenido, no de lógica, y no deberia obligar a
tocar el archivo del agente."""

import unicodedata


SALUDOS = {"hola", "holaa", "buenas", "buenosdias", "buenastardes", "buenasnoches",
           "hey", "quetal", "saludos", "holabuenas"}

CAPACIDADES = ("quepuedeshacer", "quesabeshacer", "enquemepuedesayudar",
               "enquepuedesayudar", "quieneres", "paraquesirves",
               "quemepuedesofrecer")

BIENVENIDA = (
    "Soy el asistente de COLFLUX, la plataforma que monitorea gases de efecto invernadero, "
    "carbono y biomasa en ecosistemas no forestales de Colombia: páramos, humedales, "
    "sabanas inundables y morichales.\n\n"
    "Puedo consultar por ti:\n"
    "- Mediciones de flujo de CO2 y CH4, con su fecha, su valor y su unidad, en un sitio, "
    "una vereda, un municipio o un departamento.\n"
    "- Carbono orgánico del suelo, biomasa y materia orgánica muerta.\n"
    "- Variables ambientales: temperatura del suelo y del aire, humedad, presión y nivel "
    "de agua.\n"
    "- Qué sitios de monitoreo existen y dónde están, incluso buscando por coordenada.\n"
    "- Qué significa un término del trabajo de campo, como cuando alguien dice que el "
    "suelo está respirando fuerte.\n\n"
    "Pregúntame por un lugar y un dato, o por un término que no conozcas."
)


def _clave(texto: str) -> str:
    base = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(c for c in base if c.isalnum())


def respuesta_fija(texto: str) -> str:
    """Saludos y preguntas sobre qué es el asistente: respuesta fija, sin llamar al
    modelo. Cuesta cero tokens, sale siempre completa y evita que el prompt tenga que
    cargar instrucciones de presentación en todas las llamadas."""
    k = _clave(texto)
    if not k:
        return ""
    if k in SALUDOS:
        return BIENVENIDA
    # La frase tiene que ser casi todo el mensaje. Con mucho texto alrededor,
    # que puedes hacer es parte de una pregunta real: no sé qué puedes hacer
    # con los datos de Caldas no es alguien pidiendo la presentación.
    if any(c in k and len(k) <= len(c) + 10 for c in CAPACIDADES):
        return BIENVENIDA
    return ""
