"""Qué se puede consultar, separado de la lógica que lo consulta.

Añadir una categoría o reescribir su descripción es un cambio de contenido, no de
código. Las descripciones no son decorativas: son lo que ve el modelo cuando pide
algo que no existe, así que tienen que decir qué mide cada cosa, no solo cómo se
llama."""

# Categorías de /api/geo/resumen/?categoria=
CATEGORIAS = {
    "flujos": "flujos de gases de efecto invernadero",
    "biomasa": "carbono almacenado en biomasa",
    "cos": "carbono orgánico del suelo",
    "produccion": "producción de biomasa en gramos",
}

# Vistas de /api/proyectos/<id>/datos/?vista= que no son categorías del resumen
VISTAS = {
    "mom": "materia orgánica muerta: hojarasca y restos vegetales",
    "clima": ("variables ambientales medidas junto a los flujos: temperatura del suelo "
              "y del aire, presión atmosférica, humedad relativa, punto de rocío y nivel "
              "de agua"),
}
