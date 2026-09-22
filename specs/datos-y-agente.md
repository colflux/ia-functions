# Herramientas de datos y comportamiento del agente

## Contexto

El servidor MCP exponía tres herramientas sobre la API geográfica: `listar_sitios`,
`consultar_promedio` y `consultar_ultima_medicion`, las tres limitadas a flujos de gases.

Al probarlas contra los datos reales de la plataforma —113 sitios, 7.048 submuestras y
646 registros de biomasa— aparecieron dos clases de fallo.

La primera es de alcance: no había forma de consultar carbono orgánico del suelo, biomasa,
materia orgánica muerta ni variables ambientales, ni de acotar una consulta por vereda,
municipio, departamento o coordenada.

La segunda es más seria. Cuando una herramienta no podía responder, **no lo decía**:
devolvía un dato parcial, ignoraba un argumento que había recibido, o afirmaba que algo no
existía cuando en realidad no se podía consultar así. El modelo llenaba ese hueco con algo
verosímil. En una plataforma científica ese es el peor modo de fallo posible, porque la
cifra suena razonable y nadie la revisa.

Ninguno de los fallos del segundo grupo se manifiesta con la base vacía, que es como se
desarrolló la integración.

## Problema 1 — solo se podían consultar flujos de gases

El equipo desplego `?categoria=flujos|biomasa|cos` en `resumen_geografico`, y
`/api/proyectos/<id>/datos/?vista=` cubre materia orgánica muerta y clima. Nada de eso
estaba expuesto al agente.

**Corrección.** `consultar_promedio` y `consultar_ultima_medicion` aceptan `categoria`. Se
añade `consultar_datos_campo` para las vistas que no son categorías —materia orgánica muerta
y variables ambientales—, que traduce las claves `Modelo.campo` a los nombres legibles que
el propio backend describe y descarta columnas vacias. Los filtros de fecha admiten un día,
un mes o un año: `2021-10-05`, `2021-10` o `2021`.

Se añade además `consultar_mediciones`, que devuelve **mediciones individuales con su
unidad** para un sitio, una vereda, un municipio, un departamento o una coordenada. No
agrega nada: cada medición conserva la unidad con la que se guardo.

## Problema 2 — la búsqueda no encontraba lo que la gente escribe

La comparación era literal, ignorando solo mayúsculas:

```python
if filtro and filtro.lower() not in " ".join([nombre, vereda, municipio]).lower():
```

Los sitios se llaman `SanAntonioParamos` y `ChocolatalBInt`. Nadie los escribe así, de modo
que "san antonio paramos" no encontraba nada y el agente respondía que el sitio no existe.

**Corrección.** Ambos lados se normalizan —minúsculas, sin tildes y sin espacios— antes de
comparar, en `mcp_server/texto.py`. Tambien se acepta un identificador numérico en todas las
herramientas, no solo en algunas, y se añade búsqueda por coordenada, calculada localmente
con la latitud y longitud que el endpoint de sitios ya devuelve.

El nivel geográfico que se ofrece pasa a depender de **cuántas opciones quedan**, no de que
parámetros llegaron: indicar municipio y vereda ya no vuelve a preguntar por el departamento.

## Problema 3 — cualquier numero del texto se tomaba como identificador

```python
m = re.search(r"\b(\d+)\b", str(nombre))
```

Con la consulta "san antonio paramos 4.8602780 -75.3886110" esa expresión captura el **4** y
devuelve el sitio con identificador 4. Sin error y sin aviso: datos de otro lugar presentados
como los pedidos.

**Corrección.** El numero solo cuenta como identificador si no forma parte de un decimal.

## Problema 4 — se promediaban unidades incompatibles

`resumen_geografico` devuelve `unidad: null` cuando el grupo que agrega mezcla unidades. Es
una señal correcta, pero la herramienta devolvía el promedio igualmente. En el sitio 158 eso
daba **-0,5967**, que sale de sumar 850 mediciones en `g/m2/h` con 127 en `umol/m2/s` y
dividir entre 977.

**Corrección.** Ante esa señal, la herramienta baja a las mediciones individuales y calcula
promedio, minimo y maximo **dentro de cada unidad**:

```
g_m2_h      850 mediciones   promedio -0,0378   min -4,18    max 1,85
umol_m2_s   127 mediciones   promedio -4,3371   min -309,0   max 51,0
```

Nunca devuelve una cifra única cuando el grupo mezcla unidades.

> Este mismo defecto esta hoy en `resumen_geografico`, y afecta a las cifras que el geoportal
> publica. Corregirlo exige decidir la unidad canónica de cada gas, que es una decisión del
> equipo, así que va en un informe aparte y no en este PR.

## Problema 5 — las herramientas aceptaban y negaban en silencio

Tres casos del mismo patrón, todos observados en conversaciones reales.

**Argumentos ignorados.** `consultar_datos_campo` sin proyecto devolvía solo el recuento por
proyecto y descartaba `sitio` y `fecha` sin mencionarlo. El modelo reintentaba con los mismos
argumentos, recibia lo mismo, y acababa presentando el total del proyecto como si fuera del
lugar preguntado. Ahora, cuando solo hay un proyecto con datos, se usa ese —lo que además
ahorra una vuelta entera del agente— y los formatos de fecha se validan: `2020-2026` se
colaba como filtro y no casaba con nada.

**Un nivel geográfico confundido con un sitio inexistente.** Pedir datos de "Cundinamarca"
respondía que no hay ningún sitio con ese nombre, que suena a error de escritura. Ahora la
herramienta reconoce que es un departamento y lo dice, junto con que esa vista no admite
filtro geográfico —sus columnas solo traen latitud y longitud—.

**Un nombre que corresponde a varios registros.** Un mismo lugar puede estar registrado como
varios `Sitio` con series de medición distintas: "calostros" coincide con cinco. La consulta
se rendía devolviendo una lista de candidatos de nombre casi idéntico y sin identificador,
con la que no se podía elegir. Ahora se consultan todos y se suman, que es lo que espera
quien pregunta por el lugar.

## Problema 6 — el coste fijo de cada llamada al modelo

Las declaraciones de herramientas viajan en **todas** las llamadas al modelo, se usen o no, y
una pregunta encadena varias. Medido con la traza que se añade en este PR:

```
sistema 538 · herramientas 1.535 · total 2.314 tokens por llamada
```

De ahí que el plan gratuito de Groq, con 8.000 tokens por minuto, se agotara con tres
preguntas seguidas.

**Corrección.** `ToolRouter` compara la pregunta con la descripción de cada herramienta usando
el modelo de embeddings que ya corre en el servicio —local, sin coste externo— y declara solo
las tres más cercanas. Las descripciones se embeben una vez y quedan en caché. Ante cualquier
fallo declara todas: gastar de más es preferible a dejar al agente sin la herramienta que
necesitaba.

Con eso y con el prompt recortado, el coste fijo baja a **941 tokens por llamada**.

Se probó tambien retirar las declaraciones después de la primera herramienta, que ahorraba
otros 680 tokens. **Se descartó:** con el historial lleno de llamadas, el modelo imitaba el
protocolo en texto plano e inventaba resultados. El ahorro no compensaba la pérdida de
fiabilidad.

## Problema 7 — el proveedor estaba fijo dentro del adaptador

El adaptador construía el cliente con la URL de Groq escrita en el código, así que cambiar de
proveedor obligaba a escribir un adaptador nuevo y duplicar todo lo demas.

**Corrección.** `OpenAICompatibleProvider` recibe URL, clave y modelo por configuración.
`GroqProvider` y `CerebrasProvider` son dos líneas cada uno y comparten el resto. Cambiar de
proveedor pasa a ser una variable de entorno.

## Comportamiento del agente

**`temperature=0`.** No se fijaba, así que se usaba la del proveedor. La misma pregunta seguía
razonamientos distintos: una pregunta compuesta consultaba el diccionario una de cada tres
veces. Con temperatura cero, tres de tres.

**`tools` y `tool_choice` se omiten** cuando no hay herramientas que declarar. Enviarlos en
nulo devuelve un 400 que desde fuera parece una respuesta vacía.

**Saludos sin modelo.** Un saludo o un "que puedes hacer" se responden con un texto fijo, sin
llamada al modelo. Coste cero y respuesta siempre completa.

**Trazas.** Cada llamada a herramienta queda registrada con sus argumentos y su resultado, y
cada llamada al modelo con su coste repartido. Antes no había ninguna observabilidad del
agente: cada comportamiento raro se diagnosticaba probando las herramientas a mano.

**Errores de cuota legibles**, distinguiendo el limite por minuto del diario, y el 402 de una
cuenta sin plan activo.

## Verificación

Todo se probó en un entorno aislado que consulta datos reales a través de su propio backend,
con contenedores y base de datos propios. Producción no se tocó.

Cada herramienta se ejecutó primero por separado, sin gastar modelo, y después por el chat
completo. Algunas comprobaciones:

- temperatura del suelo el 5 de octubre de 2021 → 14,10 a 15,00 grados
- COS por departamento → Cundinamarca 18,37 % (765 mediciones), Caldas 8,14 % (1.003)
- CO2 de la vereda Mundo Nuevo → 1.037 mediciones en 5 sitios, 910 en g/m2/h y 127 en umol/m2/s
- coordenada 4.676372 / -73.787781 → sitio lagunaseca a 0 km, 572 mediciones
- materia orgánica muerta en calostros → 84 registros sumando los cinco sitios homónimos

## Fuera de alcance

- **Búsqueda híbrida.** Los embeddings no distinguen términos exactos como códigos de sitio o
  fórmulas químicas. PostgreSQL trae búsqueda de texto con diccionario en español; combinar
  ambos rankings cubriría ese punto ciego.
- **Herramientas de escritura.** Siguen sin registrar mientras el backend no exponga sus
  endpoints.
- **El enrutador solo mira la pregunta**, no el historial, así que un seguimiento como "y el
  promedio?" puede elegir mal las herramientas.
- **La caché de identificadores geograficos no caduca**: si el backend añade sitios, el
  servidor MCP no se entera hasta reiniciarlo.
- **La protección contra unidades mezcladas cubre solo los flujos**, no biomasa ni COS.

