# Capacidad y precisión del asistente

## Problema

La batería de pruebas en producción (24 de septiembre de 2026) mostró:

1. **Esperas de 55 a 120 segundos.** El plan gratuito de Cerebras acepta unas
   5 peticiones por minuto y cada pregunta hace dos o tres. Al llenarse, la
   librería de OpenAI reintentaba en silencio.
2. **«El sitio con más metano» se respondía con una muestra** de las 10
   mediciones más recientes.
3. **Términos del diccionario respondidos de memoria** («turba desnuda» existe
   en el diccionario y no se consultó).
4. **Datos de campo sin registros** (MOM): decía que había registros y pedía filtros.
5. **«SWAMP» pasado como sitio** en lugar de proyecto.
6. **Consultas por coordenada truncadas** a 8 sitios sin avisar, cuando había
   más de 12 a menos de 50 m.
7. **«Sitio 254»** no se reconocía como número de sitio.
8. **Preguntas fuera de tema** (el Mundial) se respondían.

## Cambios

| # | Dónde | Qué |
|---|-------|-----|
| 1 | `adapters/llm/openai_compatible.py`, `failover.py`, `gemini.py`, `container.py`, `config.py` | Sin reintentos (tiempo máximo 45 s). Cuota, crédito, error del servidor o conexión → `ProveedorNoDisponible`. `FailoverProvider` prueba `LLM_PROVIDER` y luego `LLM_RESPALDO` (p. ej. `groq,gemini`); si todos fallan, avisa que el servicio está saturado. Gemini pasa a usar su endpoint compatible con OpenAI (se quita `google-genai`). |
| 2 | `mcp_server/tools/mediciones_gei.py` | Cada sitio trae `maximo_por_unidad`, calculado sobre todas las filas. Si hay más filas que la muestra, `nota_muestra` indica que no sirve para máximos, mínimos ni promedios. |
| 3 | `agent_orchestrator.py` | Antes del primer turno se consulta el diccionario; los términos con parecido ≥ 0,86 (máximo 2) se entregan al modelo como si los hubiera pedido. |
| 4 | `mcp_server/tools/datos.py` | Sin registros en ningún proyecto → `sin_datos` y nota explícita. |
| 5 | `mcp_server/tools/datos.py` | Si el «sitio» coincide con el inicio del nombre de un único proyecto y no se dio proyecto, se usa como proyecto. |
| 6 | `mcp_server/tools/mediciones_gei.py` | Por coordenada se consultan todos los sitios a menos de 0,5 km del más cercano (hasta 40); si hay más, `aviso`. |
| 7 | `mcp_server/tools/mediciones_gei.py` | `254` o `sitio 254` se busca por id. |
| 8 | `agent_orchestrator.py` | Regla en el prompt: fuera de tema, lo dice en una frase. |
| 9 | `adapters/llm/gemini.py` | Gemini 3 exige la «firma de pensamiento» en cada llamada a herramienta del historial: se reenvía la suya, y las de otros proveedores van con el valor que Google documenta para historiales ajenos. |
| 10 | `openai_compatible.py`, `failover.py` | Un 400 de un proveedor (formato que no acepta, llamada que no logró armar) también pasa al siguiente en vez de devolver error 500. Tras un 429 el proveedor queda en pausa lo que pida (20 s si no dice; máximo 120). |
| 11 | `mcp_server/tools/*.py` | El sitio se acepta como número (`105`) además de texto; antes fallaba la validación. |
| 12 | `mcp_server/tools/datos.py` | Si piden «carbono orgánico del suelo» o «biomasa» a los datos de campo, indica qué herramienta y categoría usar. |
| 13 | `mcp_server/tools/mediciones_gei.py` | `mayor_por_unidad`: el mayor valor de cada unidad y su sitio, ya calculado. En la prueba el modelo comparó nmol con umol «convirtiendo» mal. Regla en el prompt: nunca convertir entre unidades. |
| 14 | `mcp_server/tools/mediciones_gei.py`, `mediciones.py` | Si el «sitio» es exactamente una vereda, municipio o departamento («Guatavita», «Cundinamarca»), se consulta como tal. `consultar_promedio` acepta vereda, municipio y departamento (flujos); biomasa, COS y producción siguen agrupándose por departamento y lo avisa. |
| 15 | `agent_orchestrator.py` | `listar_sitios` se declara siempre: ante «¿dónde queda Calostros?» el enrutador no la elegía y el modelo contestaba que no sabía. Regla: buscar el nombre antes de decir que no hay información. |
| 16 | `agent_orchestrator.py` | Si una expresión no está en el diccionario, lo dice sin agregar una interpretación propia. |

## Configuración

```
LLM_PROVIDER=cerebras
LLM_RESPALDO=groq,gemini
GEMINI_MODEL=gemini-3.5-flash-lite
```

Requiere reconstruir la imagen de `api` (cambian las dependencias).

## Capacidad

Planes gratuitos observados en la batería del 24 de septiembre: Cerebras
rechaza hacia la 5.ª petición por minuto, Groq por tokens por minuto (cada
llamada lleva varios miles) y Gemini Flash-Lite respondió 503 «alta demanda».
Con los tres se atienden unas pocas preguntas por minuto; ante picos mayores el
asistente avisa en segundos en vez de hacer esperar uno o dos minutos. Para
más capacidad hay que pasar a un plan de pago en alguno.

## Verificación

`pruebas-ia/bateria.py <url> <pausa>` contra el laboratorio (con pausa entre preguntas, como uso real), comparando con la corrida de
producción. En los registros: `LLM <nombre> |` indica qué proveedor atendió,
`LLM no disponible` cada salto y `DICCIONARIO PREVIO` cada consulta anticipada.
