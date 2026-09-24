# Subida con lugar y ubicación, e imágenes y datos relacionados con el diccionario

## Contexto

Al probar la subida de archivos desde el chat aparecieron cuatro huecos:

1. Si alguien decía que iba a subir «la foto de un carro», el chat lo dejaba
   seguir hasta elegir el archivo; solo después se rechazaba.
2. Si el archivo no coincidía con lo descrito, había que empezar de nuevo.
3. No quedaba registrado de dónde venía la información.
4. El diccionario de campo solo se usaba si el modelo decidía consultarlo: ni
   las imágenes ni los datos se relacionaban con él.

## Cambios

### Subida en tres pasos, cada uno revisado

- `POST /documentos/descripcion` — el modelo decide solo con la descripción si
  lo que se va a subir tiene que ver con COLFLUX. Si no, el chat no sigue. Si
  el modelo no responde, se deja seguir: el archivo se revisa igual después.
- `POST /documentos/lugar` — lugar obligatorio: coordenadas dentro de Colombia,
  o municipio y departamento (`domain/services/lugar.py`). El departamento se
  compara con la lista oficial; municipio y vereda, con los que tienen sitios en
  la plataforma. Uno que la plataforma no conoce se acepta con aviso. Con
  coordenadas se informa el sitio más cercano.
- `POST /documentos` — recibe además `lugar`, y `latitud`, `longitud` y
  `precision` del dispositivo. Si el archivo no es el descrito responde
  `reintentar: true` y el chat deja elegir otro sin repetir los pasos
  («describir» y «lugar» permiten cambiar lo dicho).

### Ubicación, con su origen

Se guardan juntas en la descripción del archivo (bucket e índice):
el lugar escrito y su interpretación; el GPS que traen las fotos del celular
(`gps_foto.py`, EXIF); y la ubicación del dispositivo al subir, que el navegador
solo entrega en páginas seguras (https o localhost). En producción funcionará
cuando la plataforma tenga HTTPS; mientras tanto se omite sin error.

### Imágenes relacionadas con lo que ya existe

El revisor de imágenes devuelve también `observaciones`: rasgos visibles en
lenguaje de campo («suelo negro», «agua color té»). Cada una se busca en el
diccionario (parecido ≥ 0,86) y, si hay coordenadas, se consultan las
mediciones de CO2 y CH4 cercanas. La respuesta lo presenta como referencia: la
foto sola no permite saber el valor de un gas.

### Datos relacionados con el diccionario

`reglas_diccionario.py` lee las reglas cuantitativas simples del diccionario
(un gas contra un umbral, en µmol/m²/s y en su otra unidad). Cuando
`consultar_ultima_medicion` o `consultar_mediciones` devuelven un valor que
cumple una, el orquestador lo añade al resultado y el modelo lo menciona en una
frase («según el diccionario, con ese valor es posible sentir olor a huevo
podrido»). La regla la evalúa el código, comparando en la misma unidad del dato,
nunca convirtiendo. Las reglas que combinan luz u otro gas no se usan.

### Diccionario v2

`python -m app.scripts.cargar_diccionario <xlsx> [--aplicar]` reemplaza los
términos `diccionario-*` por los del Excel, un fragmento por término, en una
transacción, después de calcular todos los vectores. Sin `--aplicar` solo
muestra lo que haría. No toca los diccionarios subidos desde el chat.

De paso: las fuentes del diccionario ya no se recortan como si fueran archivos
del bucket («Olor a huevo podrido / podrido» salía como « podrido»).

## Verificación (laboratorio)

- «foto de un carro» → rechazado en el primer paso.
- Lugar «paramo» → pide municipio y departamento; «4.91, 73.73» → acepta y
  corrige el signo; vereda de otro municipio → aviso.
- Archivo que no coincide → se elige otro sin repetir la descripción.
- Foto con GPS → la descripción guarda las tres ubicaciones.
- Foto de suelo oscuro → término «Suelo negro» del diccionario y mediciones
  cercanas.
- Última medición de CH4 ≥ 0,0735 µmol/m²/s → una frase sobre el olor a huevo
  podrido, con el diccionario como fuente.
