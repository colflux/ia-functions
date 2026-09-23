# Subida de archivos desde el chat

## Introducción

Este cambio permite que las personas con nivel **reportador** (o administrador) suban
archivos desde el chat del asistente: entrevistas, términos del diccionario, tablas de
datos, documentos relacionados e imágenes. El asistente revisa cada archivo antes de
guardarlo, lo guarda en el bucket de Lightsail y en la base de conocimiento, y después lo
puede consultar, citar, mostrar y ofrecer para descarga.

Acompaña al PR del frontend `feat/subida-documentos`, que agrega el botón 📎, la
conversación de subida, las miniaturas de imágenes y el botón «Descargar».

## Contexto

- El conocimiento de campo (entrevistas, términos locales, fotos, tablas sueltas) no tenía
  cómo entrar a la plataforma sin pasar por el equipo técnico.
- La única vía era `POST /ingest`, que recibe texto en un JSON **sin ninguna
  autenticación** y estaba expuesta a internet en `/ia/ingest`. Se cerró en Nginx de
  producción (`location = /ia/ingest { return 403; }`); el frontend no la usaba.
- Subir sin revisar llenaría la base de conocimiento de material ajeno a COLFLUX, y una
  tabla de datos sin unidad, fecha o coordenadas repetiría el problema de unidades mezcladas
  que ya afectó los promedios del geoportal.

## Qué hace

### Quién puede subir

El chat envía el token de sesión (`Authorization: Token …`). El asistente lo verifica
contra `GET /api/auth/me/` del backend y exige nivel `reportador` o superior, con la misma
cascada que `Usuario.tiene_nivel`. El navegador nunca decide el nivel: el chat solo avisa
antes de enviar.

### Flujo de subida

1. Sin sesión, el 📎 está deshabilitado.
2. Primer toque: el chat pregunta qué se va a subir (tipo y de qué trata).
3. Segundo toque: se elige el archivo. Viaja con la descripción (`descripcion`).
4. El asistente:
   - rechaza duplicados por su huella sha256, sin llamar al modelo;
   - extrae el texto (PDF, Word `.docx`, Excel `.xlsx`, CSV, texto) o, si es imagen
     (JPG, PNG, WebP), la revisa con un modelo con visión;
   - pide al modelo que decida si tiene relación con COLFLUX, de qué tipo es y si
     **coincide** con la descripción. Si no, no se guarda y se explica por qué;
   - si es de tipo `datos`, lo valida contra el modelo de datos (siguiente sección).
5. Si se acepta, guarda el original en el bucket y el texto en la base de conocimiento.

Límites: 25 MB por archivo (15 MB las imágenes) y 300.000 caracteres de texto.

### Validación de archivos de datos

La lista de campos obligatorios sale del ETL (`GET /api/etl/campos-destino/`), así que si
el modelo de datos cambia, la validación cambia con él.

| Categoría | Campos obligatorios |
|---|---|
| Flujos de gases | sitio, latitud, longitud, fecha, gas, valor, unidad, condición de luz |
| Clima, suelo, biomasa, MOM | sitio + los campos que el modelo marca como obligatorios |

- Las coordenadas son obligatorias en flujos porque un mismo nombre de sitio agrupa
  parcelas y tubos en puntos distintos.
- En las demás categorías, si el sitio **no existe** en la plataforma
  (`GET /api/geo/sitios/`, por nombre, id o unidad de muestreo), se piden sus coordenadas.
- El modelo propone qué columna corresponde a cada campo, y el código comprueba que esa
  columna exista de verdad en el archivo.
- Si falta algo, no se guarda nada: la respuesta trae `pendiente: true` y las preguntas.
  El chat reenvía el archivo con las respuestas de la persona (`complementos`) hasta que
  esté completo o la persona escriba «cancelar».

Por ahora los datos quedan como **texto consultable**, no como mediciones: más adelante
deberán pasar por el ETL.

### Dónde queda cada cosa

| Lugar | Qué guarda | Para qué |
|---|---|---|
| Bucket `documentos/<tipo>/<fecha>-<id>-<nombre>` | el archivo original | descargar y mostrar |
| Bucket `documentos/_info/…` | descripción y datos aportados | trazabilidad |
| Bucket `documentos/_huellas/<sha256>` | clave del archivo y cómo se guardó | evitar duplicados |
| Base de conocimiento (`document_chunks`) | texto en fragmentos con su embedding; la fuente es la clave del bucket | que el chat lo consulte |

Si un archivo se borra del bucket (por ejemplo, desde la consola), su huella deja de
contar y se puede volver a subir.

### Consulta desde el chat

- Herramienta nueva `consultar_archivos_subidos`: sin argumentos lista los archivos; con
  `archivo` lee uno; con `terminos` busca texto exacto (fechas, coordenadas, sitios) sin
  distinguir tildes, y devuelve las filas que coinciden con el encabezado de su tabla. La
  búsqueda por significado de `buscar_documentos` no sirve para eso.
- **Consultas encadenadas**: una herramienta puede pedir otra consulta con la clave
  `consultar_tambien`, y el orquestador la ejecuta sin esperar al modelo. Se usa en dos
  casos: si `consultar_mediciones` no encuentra datos en la plataforma, se buscan en los
  archivos subidos; y una búsqueda corta de documentos («entrevistas») trae además la lista
  completa. Solo con la indicación en el prompt, el modelo le decía a la persona que usara
  la herramienta en vez de usarla.
- Cuando un dato sale de un archivo subido, el asistente lo aclara: no pasó por el ETL.

### Descargas e imágenes

- `GET /documentos/descargar?archivo=…`: enlace temporal de 1 hora al original. Solo
  reportadores y administradores. Nunca entrega huellas ni descripciones internas.
- `GET /documentos/imagen?archivo=…`: enlace temporal para **ver** una imagen dentro del
  chat. Abierto a cualquiera; descargar el original sigue pidiendo nivel.

### Respuestas del chat

- `GET /bienvenida`: el texto de presentación vive solo en `bienvenida.py`; el chat lo
  muestra al abrirse.
- Respuestas fijas, sin llamar al modelo, para «¿puedo subir información?» y
  «¿dónde lo descargo?».
- Prompt: no inventar secciones, menús ni niveles de la plataforma; ser puntual; no afirmar
  relaciones que los datos no muestran. Como red de seguridad se quitan las tablas y las
  marcas de cita (`【…】`) de la respuesta.
- El mensaje de respaldo, cuando el agente agota sus vueltas, ya no afirma haber consultado
  el diccionario.

## Plan por fases

1. **Subida con permisos**: `POST /documentos`, verificación de nivel contra el backend,
   extracción de texto, revisión con el modelo, bucket y base de conocimiento.
2. **Validación de datos** contra el modelo del ETL y el flujo de preguntas por el chat.
3. **Descripción previa, duplicados e imágenes** (Gemini).
4. **Descargas** para reportadores y administradores.
5. **Consulta de archivos subidos** y consultas encadenadas.
6. **Imágenes visibles en el chat**.

## Configuración y despliegue

Variables nuevas en el `.env` (documentadas en `.env.example`):

| Variable | Uso |
|---|---|
| `BUCKET_NAME`, `BUCKET_REGION`, `BUCKET_ACCESS_KEY_ID`, `BUCKET_SECRET_ACCESS_KEY` | bucket de Lightsail. Sin `BUCKET_NAME`, la subida responde que no está disponible |
| `MAX_UPLOAD_MB` | tamaño máximo por archivo (25) |
| `GEMINI_API_KEY`, `IMAGE_MODEL` | revisión de imágenes (`gemini-3.5-flash-lite`). Sin clave, las imágenes no se aceptan |
| `BACKEND_API_BASE_URL` | ahora también la usa el servicio `api`, no solo el MCP |

Para desplegar:

- **Reconstruir la imagen** (`docker compose build`): hay librerías nuevas
  (`python-multipart`, `pypdf`, `python-docx`, `openpyxl`, `boto3`).
- El servicio `api` recibe `extra_hosts: host.docker.internal:host-gateway` para llegar
  al backend.
- La clave del bucket se crea en Lightsail → Storage → bucket → Permissions → Access keys.
- Nginx ya acepta 25 MB (`client_max_body_size 25m`) y enruta `/ia/documentos…` por el
  bloque `/ia/` existente.

## Verificación

Probado en el laboratorio contra el backend, el bucket y los modelos reales:

- Ciudadano → 403 con el mensaje de nivel; sin sesión → 401; JPG no permitido antes de
  habilitar imágenes → 415.
- Una receta y una cotización se rechazan con su motivo; una entrevista en PDF y una en
  texto se guardan y el chat responde con su contenido.
- Una entrevista descrita como «datos de clima» se rechaza por no coincidir.
- El mismo archivo con otro nombre se rechaza como duplicado sin llamar al modelo; tras
  borrarlo del bucket, se puede volver a subir.
- Excel de flujos sin unidad ni condición de luz → las pide; responder solo una sigue
  pidiendo la otra; «cancelar» descarta. Sin coordenadas → pide latitud y longitud. Clima
  con un sitio inexistente → pide sus coordenadas; con sitios existentes → se guarda.
- «dame el co2 en 4,676372 -73,787781 2025-10-07» → sin datos en la plataforma, el
  orquestador busca en los archivos y responde con las dos filas del Excel, aclarando que
  no pasaron por el ETL.
- Una foto de trabajo de campo se acepta y se ve en el chat; una foto sin relación se
  rechaza.
- Imagen inexistente → 404 «Esa imagen ya no está disponible».
- Pendiente de verificar antes de fusionar: que el botón «Descargar» baje el original para un
  reportador y no aparezca para un ciudadano.

## Limitaciones y pendientes

- Los datos subidos no se convierten en mediciones: quedan como texto hasta que pasen por
  el ETL.
- Los PDF escaneados no traen texto; hay que pasarlos antes por OCR.
- No hay forma de borrar un archivo subido desde la plataforma: hoy hay que limpiar el
  bucket y la base de conocimiento a mano.
- El chat todavía no envía quién escribe en `/chat`, así que las personas sin sesión
  comparten el historial de conversación (`anonimo`). Se corrige aparte.
- `POST /ingest` sigue en el código; queda cerrado solo por Nginx.
