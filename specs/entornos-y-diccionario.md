# Laboratorio y producción separados en el bucket, términos del diccionario y lugares sin datos

## Introducción

Tres ajustes tras probar en producción el PR de mediciones por chat y Excel:

1. El laboratorio y producción dejan de compartir las claves del bucket.
2. El diccionario reconoce una expresión aunque se diga con otras palabras
   («¿es normal que huela a huevo podrido?» → «Olor a huevo podrido»).
3. Al listar sitios no se nombran lugares sin mediciones.
4. El Excel y la frase del diccionario sobre un dato los agrega el chat, no el modelo.

## Contexto

- **Bucket compartido.** Laboratorio y producción usan el mismo bucket y las mismas
  claves (`documentos/…`). Un archivo subido en el laboratorio dejaba su huella en
  `documentos/_huellas/`, y producción, que no lo tenía indexado, respondía «ya se
  había subido antes» y no lo guardaba; luego decía que no tenía archivos. Además,
  el depurador de un entorno veía los archivos del otro.
- **Diccionario.** «¿Es normal que huela a huevo podrido?» respondía que el término
  exacto no estaba: el parecido por significado no llegó al umbral de la consulta
  anticipada y la regla de «término exacto» se aplicó de forma literal.
- **CH4 sin relación con el diccionario en producción.** Las reglas cuantitativas
  vienen del diccionario v2, que en producción aún no se había cargado (sigue el v1,
  sin reglas).
- **Lugares sin datos.** «¿De qué sitios en Colombia tienes datos?» respondía «76
  sitios en Cundinamarca y ninguno en Caldas»: se entregaban al modelo todos los
  departamentos, también los de cero mediciones.

## Qué hace

### Separación en el bucket

- `BUCKET_PREFIJO` (vacío en producción, `lab/` en el laboratorio): el adaptador del
  bucket antepone el prefijo a todas las claves. La base y el resto del código no
  cambian.
- La huella de un archivo solo cuenta si **esta** base lo tiene indexado; si no, se
  permite subirlo (`RagService.tiene_fuente`).
- `app/scripts/mover_al_prefijo.py`: copia al prefijo los archivos que la base del
  laboratorio tiene indexados (original, descripción y huella). Solo copia.

### Diccionario

- La consulta anticipada también toma un término si la pregunta contiene sus
  palabras propias (sin las genéricas «olor», «color», «agua»…): con dos o más
  palabras vale en cualquier pregunta; con una sola, solo en preguntas de
  definición.
- Regla en el prompt: la misma expresión dicha con otras palabras se trata como ese
  término; solo un término distinto se presenta como «parecido».

### Lugares sin datos

- `listar_sitios` ya no pone en `elige_uno_de` los lugares con cero mediciones;
  informa su total en `registrados_sin_mediciones` y la nota pide no mencionarlos
  ni ofrecer Excel de una lista de sitios.

### Oferta del Excel

- El modelo lo ofrecía también tras una definición del diccionario o una lista de
  sitios, aunque el prompt se lo prohibía. Ahora el orquestador quita las frases del
  modelo que mencionan Excel y agrega la oferta solo si en esa respuesta una
  herramienta exportable (`consultar_mediciones`, `consultar_datos_campo`,
  `consultar_mediciones_chat`) devolvió datos.

### Frase del diccionario sobre los datos

- El CH4 del punto 4.9171580, -73.7430250 (máximo 0,0443 µmol/m²/s) cumple la regla
  de «Olor a pantano» (≥ 0,0188), pero el modelo omitía la frase cuando la respuesta
  era larga. Ahora la regla la evalúa el código y la frase también la agrega el
  código, al final de la respuesta, con el término como fuente.

## Plan por fases

1. Desplegar este cambio (producción no necesita variables nuevas).
2. Cargar el diccionario v2 en producción (`python -m app.scripts.cargar_diccionario`).
3. En el laboratorio: copiar sus archivos al prefijo, poner `BUCKET_PREFIJO=lab/` y
   reiniciar.
4. Más adelante, si se quiere, borrar de la raíz del bucket los archivos que solo
   eran del laboratorio.

## Verificación

- Prueba local de coincidencias: «es normal que huela a huevo podrido» → «Olor a
  huevo podrido»; «¿qué es un páramo?» → «Páramo»; «mediciones del páramo de
  Guerrero» → no; «dame el co2 del sitio 105» → no.
- Prueba local de `listar_sitios`: un departamento con cero mediciones no aparece.
- Laboratorio: subir en producción un archivo que el laboratorio ya había subido lo
  guarda; con el prefijo, cada entorno ve solo sus archivos.
