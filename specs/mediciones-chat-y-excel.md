# Mediciones dictadas en el chat, Excel de lo consultado y datos reales por sitio y punto

## Introducción

Este cambio hace tres cosas en el asistente:

1. Permite **registrar mediciones dictadas en el chat** («hoy medí 3,2 µmol de CO2 en el
   sitio 105»): el asistente pide lo que falte, muestra un resumen y guarda solo cuando la
   persona escribe «confirmo».
2. Permite **descargar en Excel** lo que se consulta: todas las filas, en un solo libro,
   con una hoja por consulta.
3. Hace que el asistente hable **solo de datos que existen**: distingue sitios registrados
   de sitios con mediciones, no presenta registros vacíos como datos, responde el punto
   exacto de una coordenada y deja de citar archivos borrados del bucket.

Acompaña al PR del frontend `feat/excel-chat`, que muestra el botón «Descargar Excel».

## Contexto

Revisando producción después del PR #10 (24 de septiembre de 2026):

- «¿De qué sitios tienes datos?» respondía «107 sitios». Son los sitios **registrados**;
  con mediciones hoy solo hay 76, todos en Guatavita. `listar_sitios` contaba registros,
  no datos.
- Hay sitios con **registros de medición vacíos**: Conejeras (Caldas) tiene 16 registros de
  CO2 con valor nulo, y el asistente los presentaba como mediciones.
- Por coordenada, «el punto exacto» 4.912392, -73.737119 sumaba los **56 sitios a menos de
  250 m** (872 mediciones) y decía «a 0 km». Verificado contra la base, en laboratorio y en
  producción:

  | Distancia | Sitios | Mediciones de CO2 |
  |-----------|--------|-------------------|
  | ≤ 10 m    | 1 (sitio 77) | 36 |
  | ≤ 50 m    | 12     | 287  |
  | ≤ 250 m   | 56     | 1.235 |

- Las distancias se redondeaban a 10 m (dos decimales en km): 6 m y 14 m salían iguales.
- Los **archivos borrados** desde la consola del bucket seguían indexados y el chat los
  citaba («tienes imágenes guardadas» listaba fotos que ya no existían).
- Se pidió registrar mediciones por el chat con los mismos mínimos que la subida de datos
  (sin exigir coordenadas por tubo) y descargar en Excel lo consultado.

## Qué hace

### Datos reales por sitio

- `listar_sitios` agrega en cada nivel `con_mediciones` (y `total_con_mediciones`): cuántos
  sitios tienen al menos una medición de gases **con valor**. La nota le indica al modelo
  que responda con ese número y que no mencione listas internas. Si el backend no
  responde, no se afirma nada sobre los datos.
- `consultar_mediciones` separa los registros sin valor (`registros_sin_valor`); si todos
  lo son, responde que no hay datos medidos.
- Regla en el prompt: no ofrecer datos, sitios ni consultas que las herramientas indican
  que no existen.

### Punto exacto de una coordenada

- «En el punto» son los sitios a `radio_m` metros o menos (10 por defecto, máximo 1.000).
- Si ninguno está en ese radio, se usa el más cercano y se avisa a qué distancia está.
- Los sitios a menos de 50 m se nombran aparte (`otros_sitios_cercanos`) **sin sumar sus
  mediciones**; el asistente ofrece incluirlos con un `radio_m` mayor.
- `sitios_cerca` calcula la distancia con 4 decimales (0,1 m).

### Mediciones dictadas en el chat

Herramientas locales del asistente (`adapters/tools/mediciones_chat_tool_provider.py`):

| Herramienta | Quién la usa | Qué hace |
|-------------|--------------|----------|
| `registrar_medicion` | el modelo | Valida lo dictado y pide lo que falte; si está completo, propone un resumen |
| `guardar_medicion` | solo el orquestador | Guarda tras «confirmo»; no se le ofrece al modelo |
| `consultar_mediciones_chat` | el modelo | Consulta (y exporta) lo registrado |

- **Mínimos**: sitio, fecha, gas (CO2, CH4, N2O), valor, unidad (µmol/m²/s, nmol/m²/s o
  g/m²/h) y condición de luz (día/noche). «Hoy» y «ayer» se interpretan en hora de
  Colombia; no se aceptan fechas futuras.
- **Coordenadas solo si el sitio es nuevo**: si el sitio existe se usan las suyas. Si hay
  varios sitios con el mismo nombre, pregunta cuál.
- **Dónde se guardan**: tabla `mediciones_chat` de la base del asistente, con estado
  `pendiente_etl`. No entran a la base de la plataforma ni aparecen en mapas hasta que, en
  el futuro, pasen por el ETL.
- **Quién puede**: nivel reportador o superior. El chat envía el token de sesión con cada
  mensaje (`ChatRequest.token`); el asistente lo verifica con `GET /api/auth/me/` al
  registrar y otra vez al guardar. El token vive solo durante la petición
  (`domain/services/contexto.py`); el modelo nunca lo ve.
- **Confirmación**: `ToolRegistry` admite herramientas `ocultas`, que se pueden ejecutar
  pero no se declaran al modelo. Así el modelo no puede guardar sin que la persona
  confirme. La propuesta indica con qué herramienta se confirma (`confirmar_con`).
- Evita duplicados exactos del mismo usuario.
- Se elimina `mcp_server/tools/mediciones_chat.py`: sus herramientas
  (`proponer_guardar_medicion`, `confirmar_medicion`) nunca se registraron y siempre
  respondían «no disponible». `arquitectura.md` se actualiza con el diseño nuevo.

### Excel de lo consultado

- `consultar_mediciones`, `consultar_datos_campo` y `consultar_mediciones_chat` aceptan
  `exportar=true` y devuelven todas las filas en `exportar`.
- El orquestador **saca esas filas antes de que lleguen al modelo** (pueden ser miles) y le
  deja solo un aviso de que quedaron en el Excel.
- `domain/services/descargas.py` arma un libro con una hoja por consulta (nombres únicos,
  encabezados en negrita, columnas ajustadas), lo guarda en el bucket bajo `descargas/` y
  la respuesta del chat trae `descargas: [{archivo, nombre, filas, hojas}]`.
- `GET /descargas/excel?archivo=…` entrega un enlace temporal de 1 hora, **abierto a
  cualquiera**: son datos que el geoportal ya muestra. Solo acepta claves bajo
  `descargas/` terminadas en `.xlsx`.
- Tras responder con mediciones o datos de campo, el asistente ofrece el Excel y pregunta
  si se agregan otros datos (otro gas, fechas o sitios). No lo ofrece para listas de sitios
  ni cuando no hubo datos.
- `consultar_datos_campo` exporta hasta 500 registros por sitio y avisa si hay más.

### Archivos borrados del bucket

- `domain/services/depuracion.py` (`DepuradorArchivos`): antes de consultar archivos
  subidos o buscar en documentos, y como mucho una vez cada 5 minutos, revisa qué fuentes
  `documentos/` ya no tienen original en el bucket y borra sus fragmentos del índice.
- Si el bucket no responde, no borra nada.
- `VectorStore` gana `fuentes(prefijo)` y `borrar_fuente(fuente)`.

## Plan por fases

1. **Datos reales** (sin efectos de escritura): sitios con mediciones, registros vacíos,
   punto exacto, distancias precisas.
2. **Excel**: exportación por herramienta, libro en el bucket, ruta pública de descarga,
   botón en el frontend.
3. **Mediciones dictadas**: tabla nueva, herramientas con confirmación y verificación de
   nivel.
4. **Depuración de archivos borrados** del índice.
5. **Futuro** (fuera de este PR): pasar las mediciones de `mediciones_chat` por el ETL de
   la plataforma, con su estado de validación, y limpiar periódicamente `descargas/`.

## Verificación

Laboratorio (datos completos, IDEAM y SWAMP):

- «¿de qué sitios tienes datos?» → 94 sitios con mediciones, por departamento.
- «dame el co2 en 4.912392, -73.737119» → sitio 77, 36 mediciones, que coincide con la
  consulta directa a la base.
- «dime las mediciones de CO2 de Conejeras» → identifica los tres sitios homónimos y
  pregunta cuál.
- «¿tienes imágenes guardadas?» → retira del índice las 3 imágenes borradas del bucket y
  responde solo con el PDF que queda.
- «dame las mediciones de CO2 del sitio 105 y descárgalas en Excel» → libro de 27 filas.
- Registro por chat, pruebas locales: sin sesión, con nivel ciudadano, datos incompletos,
  sitios homónimos, fecha futura, sitio nuevo sin y con coordenadas; «confirmo» ejecuta la
  herramienta oculta con la sesión de quien confirma y un segundo «confirmo» ya no
  encuentra nada pendiente.
- Excel: hojas con nombre único, columnas unidas entre filas, enlace solo bajo
  `descargas/`.
- Frontend: eslint y `tsc -b` sin errores.

## Al desplegar

- Reconstruir `api` y `mcp-backend`. La tabla `mediciones_chat` se crea sola al arrancar.
- Sin variables nuevas en el `.env`.
- Fusionar este PR antes que el del frontend `feat/excel-chat`.
