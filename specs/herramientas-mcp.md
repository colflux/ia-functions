# Correcciones de las herramientas MCP

## Contexto

El servidor MCP expone al agente tres herramientas sobre la API geográfica del
backend: `listar_sitios`, `consultar_promedio` y `consultar_ultima_medicion`.

Al probarlas contra los datos reales de producción —113 sitios, 563 unidades de
muestreo y 7.048 submuestras— aparecieron tres fallos que impedían responder
preguntas básicas. Ninguno se manifiesta con la base vacía, que es como se
desarrolló la integración.

## Problema 1 — listar_sitios desbordaba el contexto del modelo

La herramienta devolvía los 113 sitios con su `resumen_por_gas` completo.
Medido: 19.266 tokens en una sola respuesta de herramienta, contra el límite de
8.000 tokens por minuto del plan actual de Groq. El proveedor respondía 413 y el
chat terminaba en error 500.

El agravante es que el modelo casi nunca necesita ese resumen: pide la lista
para saber qué sitios existen o cuántos hay.

**Corrección.** La herramienta devuelve `id`, `nombre`, `vereda`, `municipio` y
coordenadas. Acepta un parámetro `limite` (25 por defecto, tope 100) y expone
`total` y `mostrados` aparte, de modo que el modelo puede responder "cuántos
hay" sin recibir la lista entera.

**Cambio de comportamiento.** `resumen_por_gas` ya no viaja en esta respuesta.
Quien lo necesite debe pedirlo por la herramienta de resumen.

## Problema 2 — los sitios sin nombre eran inalcanzables

76 de los 113 sitios tienen el campo `nombre` vacío. `resolve_sitio` filtraba
únicamente por ese campo, así que el agente no podía referirse a ellos de
ninguna forma.

**Corrección.** Cuando falta el nombre se construye una etiqueta con la
ubicación —`Sitio 105 (MONQUETIVA, GUATAVITA)`—, el filtro busca también en
vereda y municipio, y `resolve_sitio` acepta un número y lo resuelve por `id`.

La causa de fondo son los datos, no el código: esos 76 sitios coinciden en
número con el conjunto inicial cargado, así que el origen parece la carga
inicial. Esta corrección los hace consultables mientras tanto.

## Problema 3 — las consultas por sitio devolvían el valor de otro sitio

`consultar_promedio` y `consultar_ultima_medicion` tomaban `features[0]` del
resumen devuelto por el backend. Cuando el resumen agrupa varios sitios, el
primero no es necesariamente el solicitado, y el orden no está garantizado.

Verificado: preguntando por el sitio 105 se obtenía el promedio del 177, sin
ningún error visible — la respuesta parecía correcta.

**Corrección.** `buscar_feature(resumen, sitio_id)` selecciona el feature cuyo
`id` coincide y devuelve `sin_datos` si no aparece. Comprobado tras el cambio:
sitio 105 → 2,3549 · sitio 177 → 0,2275.

## Verificación

Las tres correcciones se probaron contra la API de producción con el conjunto
de datos completo. No se modifica ningún dato: las tres herramientas solo hacen
peticiones GET sobre endpoints públicos ya existentes.

## Fuera de alcance

El cambio del modelo de embeddings, que se propone por separado con su propia
medición. Este documento cubre únicamente las herramientas del servidor MCP.
