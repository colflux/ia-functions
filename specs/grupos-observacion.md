# Grupos de observación en Telegram

## Introducción

Prueba con un sitio inventado, **Paramox**: un grupo de Telegram al que se unen
varias personas; el equipo envía fotos del sitio y cada persona describe lo que
percibe, por texto o por audio. Todo lo que se dice queda guardado en la base
del asistente, asociado a la foto de la que se habla, y se puede consultar desde
el chat (web o Telegram) y descargar en Excel.

El objetivo del estudio es saber cuántas descripciones cualitativas hacen falta
para relacionar lo que la gente percibe con los datos tomados un día concreto.
Esta primera fase solo recoge y ordena; la relación con los datos es la fase 2.

## Contexto

- El bot de Telegram (`telegram_bot`, servicio `telegram`) ya respondía en
  privado y, en grupos, solo cuando lo mencionan. Lee por consulta periódica,
  sin HTTPS.
- Un bot **no puede crear grupos**: los crea una persona y agrega el bot.
- Por defecto un bot en un grupo solo recibe comandos y menciones. Para que
  reciba todo hay que desactivar el modo privacidad en @BotFather
  (`/setprivacy` → Disable) y volver a agregarlo, o hacerlo administrador.
- Transcripción con Whisper abierto (faster-whisper, modelo `small`, int8 en
  CPU): sin pagar a terceros; el modelo (~480 MB) se descarga una vez a un
  volumen.

## Qué hace

1. Un administrador del grupo escribe `/observacion Paramox`. El bot registra el
   grupo con ese sitio y avisa una sola vez que desde ahora guarda lo que se
   diga. `/pausar` deja de guardar; `/resumen` muestra cuántas descripciones y
   personas hay por foto. Todos los comandos son solo para administradores.
2. Desde ahí el bot guarda **en silencio** los mensajes del grupo
   (`observaciones_grupo`): autor, hora, tipo (texto, foto, audio, consulta u
   otro), texto o pie de foto, y a qué mensaje responde.
3. Todo lo de Telegram va en el bucket aparte de lo subido desde la plataforma,
   por sitio: `telegram/<sitio>/fotos/` y `telegram/<sitio>/audios/<día>/`.
   Lo subido desde el chat de la plataforma sigue en `documentos/<tipo>/`
   (datos, documento, entrevista, diccionario, imagen). El chat muestra las
   fotos de los grupos y permite descargarlas con los mismos permisos que las
   imágenes subidas (verlas cualquiera; el original, desde nivel reportador).
   Las fotos se conservan; los audios se borran a los 5 días
   (`GRUPOS_DIAS_AUDIO`; la transcripción se queda) y los Excel que genera el
   chat (`descargas/`), a los 7, en la misma tarea diaria del bot.
   `mover_a_carpetas.py` pasa a esta estructura lo guardado antes.
4. Cada audio (nota de voz, archivo de audio o video circular) se transcribe en
   un hilo aparte con Whisper; el modelo se carga al primer audio y se suelta
   tras 15 minutos sin audios. El vocabulario del campo está en
   `telegram_bot/glosario_campo.txt` (unos 260 términos: diccionario de
   COLFLUX, zonas del proyecto y palabras comunes). Whisper solo admite un
   contexto corto, así que siempre van los ~55 marcados con `*`; si la
   transcripción trae una palabra parecida a otro término («turbeira»,
   «un dado»), se transcribe otra vez sugiriéndolo («turbera», «inundado»). No se
   reemplaza texto a mano: Whisper vuelve a oír el audio y decide. Si el bot se reinicia, al arrancar retoma los
   audios pendientes.
5. Qué se guarda: las fotos que envía el equipo (administradores del grupo),
   revisadas por Gemini (una foto ajena al proyecto no se guarda), y todo lo
   que las personas escriben o dicen sobre ellas. Un filtro con el modelo de
   lenguaje marca si cada texto o audio describe la foto (`es_descripcion`),
   sin borrar nada. No se guardan stickers, GIF, videos, documentos, emojis o
   risas sueltas, fotos de participantes ni preguntas al bot.
6. Revisión de transcripciones: respondiendo a un audio, `/transcripcion`
   muestra lo que entendió Whisper y `/corregir <texto>` guarda lo que
   realmente se dijo. La corrección va en `transcripcion_corregida` (con quién
   y cuándo) y la de Whisper no se toca, para poder medir su error. Las
   consultas usan la corregida cuando existe.
7. Cada mensaje se asocia a una foto: la foto a sí misma; una respuesta a una
   foto, a esa foto; una respuesta a otro mensaje, a la foto de ese mensaje; lo
   demás, a la última foto enviada.
8. En el chat, `consultar_observaciones_grupo(sitio, fecha, foto, ultima, exportar)`:
   sin foto devuelve una lista breve (número, fecha y hora, título y cuántas
   descripciones tiene cada foto), sin textos ni imágenes, para que no crezca con
   muchas fotos; con `foto` o `ultima` devuelve esa foto con sus descripciones,
   lo que ve la IA y la imagen. Con `exportar` va todo al Excel. Lo de Telegram
   y lo subido desde el chat son consultas distintas: preguntar por una no trae
   la otra.

## Atributos del sitio, descripción de la IA y ubicación

Para llegar a estimar un flujo a partir de lo que la gente percibe, cada
descripción se convierte en atributos del sitio con nombres fijos
(`app/domain/services/atributos_sitio.py`), que siguen las categorías del
diccionario de COLFLUX: ecosistema, cielo, temperatura, humedad del aire,
viento, agua en superficie, color del agua, burbujas, olor, suelo al pisar,
color del suelo, vegetación, estado de la vegetación, hojarasca y alteración.
Además, `certeza`: si la persona lo afirma («percibe») o lo deduce («supone»).

- Texto: la misma llamada al modelo que decide si es una descripción extrae sus
  atributos. Audio: al transcribirse, y otra vez si alguien lo corrige.
- Nada que digan las personas se borra por su contenido: lo que el modelo no
  considera descripción queda guardado con `es_descripcion = false`, para
  revisarlo después. Solo no se guardan emojis o risas sueltas, stickers,
  preguntas al bot y textos anteriores a la primera foto.
- Foto: Gemini, en la misma llamada que decide si sirve, describe la foto
  (`ia_descripcion`) y anota los atributos que se VEN con claridad
  (`ia_atributos`). Todavía no se compara con lo que dicen las personas; eso
  será la fase siguiente.
- Ubicación: Telegram no entrega a los bots la ubicación de quien escribe; solo
  la que la persona comparte. Para que sea de un toque, al registrar el grupo
  (y con `/ubicacion`) el bot publica un enlace que abre el chat privado con el
  bot y muestra el botón «📍 Compartir mi ubicación» (Telegram solo permite ese
  botón en chats privados). También sirve compartirla en el grupo
  (📎 → Ubicación) o compartir la ubicación en tiempo real, cuyas
  actualizaciones llegan como ediciones del mensaje. Cada ubicación queda en
  `ubicaciones_personas`, por persona; sus descripciones de las 12 horas
  siguientes la toman, y también las de las 12 horas anteriores que no tenían
  (`origen_ubicacion` lo dice: «compartida en privado», «en tiempo real», «…
  (después)»). Una foto enviada como archivo conserva su GPS; Telegram se lo
  quita a las fotos normales.
- `/datos <sitio> <AAAA-MM-DD> [dia|noche]`, respondiendo a una foto, guarda el
  sitio y el día reales en que se tomó, para comparar después con las
  mediciones de ese día. `/ia` muestra lo que la IA ve en la foto.
- Excel: cada foto (con lo que ve la IA y el día de los datos), cada mensaje
  (con `es_descripcion` y sus atributos) y cada ubicación compartida, con fecha,
  autor y coordenadas.
- `completar_atributos.py` calcula los atributos de lo guardado antes.

Fase siguiente (no incluida): consenso entre personas, contraste con la IA,
comparación con las mediciones del día de cada foto y estimación de un rango de
flujo a partir de los sitios medidos más parecidos.

## Piezas

| Archivo | Qué es |
| --- | --- |
| `app/domain/services/observaciones_grupo.py` | Tablas `grupos_observacion` y `observaciones_grupo` (se crean al primer uso), asociación a fotos, consulta y resumen |
| `app/api/grupos.py` | Rutas internas `/grupos/*` para el bot; exigen `X-Grupos-Token` = `GRUPOS_TOKEN` (vacío → 503) |
| `app/adapters/tools/observaciones_grupo_tool_provider.py` | Herramienta del chat |
| `telegram_bot/registro_grupos.py` | Comandos y guardado de mensajes |
| `telegram_bot/transcriptor.py` | Cola de transcripción con faster-whisper |
| `telegram_bot/grupos.py` | Cliente de las rutas `/grupos` |

Variables: `GRUPOS_TOKEN` (api y telegram), `GRUPOS_DIAS_AUDIO` (api, 5), `WHISPER_MODELO` (`small`; vacío =
no transcribir), `WHISPER_HILOS` (2). Volumen `whisper_modelos`.

## Fase 2 (no incluida)

- Relacionar cada foto con la fecha y el sitio de los datos de campo, y comparar
  las descripciones con las mediciones de ese día.
- Medir cuántas descripciones hacen falta (curva de saturación de términos por
  foto, usando el diccionario).

## Verificación

Probado con PostgreSQL real y Telegram simulado: un no administrador no puede
registrar; antes de `/observacion` no se guarda nada; textos, fotos, audios,
respuestas encadenadas y menciones quedan asociados a la foto correcta; un
mensaje repetido no se duplica; los audios de más de 5 días se borran del
bucket y conservan la transcripción; `/transcripcion` y `/corregir` funcionan
solo respondiendo a un audio y la corrección no pisa la original; tras `/pausar` no se guarda nada; las rutas
rechazan un token inválido. En el laboratorio:

1. `/observacion Paramox` en el grupo → mensaje de confirmación.
2. Enviar una foto, un texto y una nota de voz → en los logs de `telegram`
   aparece «Audio N transcrito».
3. Responder a la nota de voz con `/transcripcion`; si hay errores,
   `/corregir <texto>` y volver a pedir `/transcripcion`.
4. `/resumen` en el grupo, y en el chat: «¿qué describieron en Paramox hoy?».
