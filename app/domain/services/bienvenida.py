"""Saludos y presentación del asistente: texto fijo, sin llamar al modelo.

Vive aparte del orquestador a propósito. Ajustar una palabra clave o reescribir
la presentación es un cambio de contenido, no de lógica, y no deberia obligar a
tocar el archivo del agente. El chat también muestra BIENVENIDA al abrirse
(GET /bienvenida), así que este es el único lugar donde vive el texto."""

import re
import unicodedata


SALUDOS = {"hola", "holaa", "buenas", "buenosdias", "buenastardes", "buenasnoches",
           "hey", "quetal", "saludos", "holabuenas"}

CAPACIDADES = ("quepuedeshacer", "quesabeshacer", "enquemepuedesayudar",
               "enquepuedesayudar", "quieneres", "paraquesirves",
               "quemepuedesofrecer")

BIENVENIDA = (
    "Hola, soy el asistente de COLFLUX, el Sistema Integrado de Observación y Cuantificación "
    "de Carbono en Colombia. Aquí podrás encontrar información de ecosistemas no forestales "
    "de nuestro país, como páramos, humedales, sabanas inundables y morichales.\n\n"
    "Puedo consultar por ti:\n"
    "- Mediciones de flujos de gases de efecto invernadero, como CO2 y CH4: podrás ver la "
    "fecha de recolección, el valor y la unidad reportada.\n"
    "- Carbono orgánico del suelo.\n"
    "- Biomasa aérea y subterránea.\n"
    "- Materia orgánica muerta.\n"
    "- Variables ambientales: temperatura del suelo, temperatura del aire, humedad, presión "
    "y nivel de agua.\n"
    "- Los sitios de monitoreo existentes, incluyendo su ubicación y coordenadas.\n"
    "- El diccionario de lenguaje: aquí podrás descubrir qué significa un término del trabajo "
    "de campo, como cuando alguien dice que el suelo está respirando fuerte, y cuál es su "
    "interpretación técnica.\n\n"
    "Pregúntame por un lugar y un dato, o por un término que no conozcas."
)


# Preguntas sobre cómo subir información: un verbo de subir y algo que se sube,
# en un mensaje corto. "¿Qué datos de biomasa subieron en 2024?" no entra:
# "subieron" no es un verbo de la lista y es una pregunta sobre los datos.
VERBOS_SUBIR = {"subir", "subo", "subirte", "subirle", "subiendo", "cargar", "cargo", "cargarte",
                "adjuntar", "adjunto", "enviar", "envio", "enviarte", "compartir", "comparto",
                "mandar", "mando", "mandarte", "aportar", "aporto"}
COSAS_SUBIR = {"archivo", "archivos", "documento", "documentos", "info", "informacion", "dato",
               "datos", "foto", "fotos", "fotografia", "fotografias", "imagen", "imagenes",
               "entrevista", "entrevistas", "excel", "pdf", "word", "csv", "termino", "terminos"}
MAX_PALABRAS_SUBIR = 12

COMO_SUBIR = (
    "Sí. Si tienes nivel reportador, puedes subir archivos para que queden guardados en COLFLUX "
    "y se puedan consultar aquí en el chat:\n\n"
    "1. Inicia sesión. El botón 📎, junto a la caja de texto, se activa cuando tienes la sesión "
    "iniciada.\n"
    "2. Toca 📎 y cuéntame qué vas a subir: el tipo de archivo y de qué trata.\n"
    "3. Toca 📎 otra vez (ahora en verde) y elige el archivo.\n"
    "4. Lo reviso: debe tener relación con COLFLUX y coincidir con lo que me contaste. Si es un "
    "archivo de datos y le falta algo obligatorio, como la unidad, la condición de luz o las "
    "coordenadas, te lo pregunto aquí mismo; puedes responder o escribir «cancelar».\n\n"
    "Puedes subir entrevistas, términos del diccionario, datos de flujos de gases, clima, suelo, "
    "biomasa o materia orgánica muerta, documentos relacionados e imágenes de ecosistemas, "
    "plantas o trabajo de campo. Formatos: PDF, Word, Excel, CSV, texto, JPG, PNG o WebP, hasta "
    "25 MB (las imágenes, hasta 15 MB). Un mismo archivo no se puede subir dos veces.\n\n"
    "Los reportadores y administradores también pueden descargar los archivos subidos con el "
    "botón «Descargar» que aparece en el chat.\n\n"
    "Si todavía no eres reportador, puedes solicitar ese nivel a un administrador de la plataforma."
)

COMO_DESCARGAR = (
    "Los archivos que se suben desde el chat los pueden descargar los reportadores y "
    "administradores, con la sesión iniciada:\n\n"
    "- Al guardar un archivo, el mensaje de confirmación trae el botón «Descargar».\n"
    "- Cuando una respuesta cita un archivo subido, abre «fuentes» y usa «Descargar» junto a ese "
    "archivo.\n\n"
    "El enlace de descarga dura una hora. Si no ves el botón, revisa que hayas iniciado sesión con "
    "una cuenta de nivel reportador; puedes solicitar ese nivel a un administrador de la plataforma."
)
VERBOS_BAJAR = {"bajar", "bajarlo", "bajarla", "bajarlos", "bajarlas"}
# Solo preguntas genéricas ("¿dónde lo descargo?"). Si nombran el archivo
# ("quiero descargar la entrevista a Rosa"), las responde el modelo buscándolo
# con consultar_archivos_subidos, que es lo que hace aparecer el botón.
MAX_PALABRAS_DESCARGAR = 4


def _palabras(texto: str) -> list[str]:
    base = unicodedata.normalize("NFKD", (texto or "").lower()).encode("ascii", "ignore").decode()
    return re.findall(r"[a-z0-9]+", base)


def _pregunta_por_descargar(texto: str) -> bool:
    # "descargar" es inequívoco; "bajar" solo cuenta si habla de un archivo,
    # para no atrapar "¿por qué bajó el nivel del agua?".
    palabras = _palabras(texto)
    if len(palabras) > MAX_PALABRAS_DESCARGAR:
        return False
    if any(p.startswith("descarg") for p in palabras):
        return True
    return bool(VERBOS_BAJAR.intersection(palabras)) and bool(COSAS_SUBIR.intersection(palabras))


def _pregunta_por_subir(texto: str) -> bool:
    palabras = _palabras(texto)
    return (
        len(palabras) <= MAX_PALABRAS_SUBIR
        and bool(VERBOS_SUBIR.intersection(palabras))
        and bool(COSAS_SUBIR.intersection(palabras))
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
    if _pregunta_por_descargar(texto):
        return COMO_DESCARGAR
    if _pregunta_por_subir(texto):
        return COMO_SUBIR
    return ""
