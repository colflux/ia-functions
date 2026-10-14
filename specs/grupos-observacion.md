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
3. Las fotos van al bucket junto a las imágenes subidas desde el chat
   (`documentos/imagen/<día>-<sello>-<sitio>-foto-<mensaje>.jpg`), así el chat
   las muestra y ofrece descargarlas con los mismos permisos (verlas cualquiera,
   descargar el original desde nivel reportador). Los audios van a
   `grupos/<chat>/<día>/<mensaje>.<ext>`. Las fotos se conservan; los audios se borran a los 5 días (`GRUPOS_DIAS_AUDIO`;
   la transcripción se queda). Mientras el audio exista se puede escuchar en
   Telegram y comparar.
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
5. Solo se guarda lo que sirve al estudio: las fotos que envía el equipo
   (administradores del grupo) y las descripciones de ellas. No se guardan
   stickers, GIF, videos, documentos, fotos de participantes ni preguntas al
   bot. Cada texto pasa por un filtro (el modelo de lenguaje, con respuesta
   sí/no) que descarta saludos, risas, emojis, coordinación del grupo y temas
   ajenos; un audio se transcribe y, si no es una descripción, se borra con su
   archivo. Ante la duda o si el modelo no responde, se guarda.
   `limpiar_mensajes.py` aplica estas reglas a lo ya guardado (primero muestra,
   con `--borrar` borra).
6. Revisión de transcripciones: respondiendo a un audio, `/transcripcion`
   muestra lo que entendió Whisper y `/corregir <texto>` guarda lo que
   realmente se dijo. La corrección va en `transcripcion_corregida` (con quién
   y cuándo) y la de Whisper no se toca, para poder medir su error. Las
   consultas usan la corregida cuando existe.
7. Cada mensaje se asocia a una foto: la foto a sí misma; una respuesta a una
   foto, a esa foto; una respuesta a otro mensaje, a la foto de ese mensaje; lo
   demás, a la última foto enviada.
8. En el chat, `consultar_observaciones_grupo(sitio, fecha, foto, exportar)`
   devuelve por foto: cuántas descripciones, de cuántas personas, y los textos.
   Las descripciones son textos y audios transcritos; no cuentan el pie de la
   foto ni las preguntas al bot. Con `exportar` va al Excel. Una pregunta
   general («¿qué imágenes tienes guardadas?») consulta a la vez los archivos
   subidos desde el chat y las fotos de los grupos, y las presenta por separado.

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
