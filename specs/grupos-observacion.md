# Grupos de observación en Telegram

## Introducción

Prueba con un sitio inventado, **Paramox**: un grupo de Telegram al que se unen
varias personas; el equipo envía fotos del sitio y cada persona describe lo que
percibe, por texto o por audio. **Desde octubre de 2026 las descripciones se
hacen en el chat privado con el bot, no en el grupo**, para que nadie vea ni
copie lo que dijeron los demás (ver «Descripciones en privado»), y ahí el bot
hace preguntas sobre la foto una por una (ver «Preguntas por foto»). Todo queda
guardado en la base del asistente, asociado a la foto que se describe, y se
puede consultar desde el chat web con la sesión iniciada y descargar en Excel.

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
2. Desde ahí el bot guarda las fotos que envía el equipo
   (`observaciones_grupo`): autor, hora y pie de foto. Las descripciones se
   reciben en privado (ver «Descripciones en privado»); antes de ese cambio se
   guardaban también los textos y audios del grupo, y siguen en la base.
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

## Descripciones en privado

El grupo queda solo para que el equipo envíe las fotos y para reunir a quienes
participan. Se recomienda configurar en Telegram (Permisos del grupo) que los
miembros no puedan enviar mensajes; de todos modos el bot solo guarda, del
grupo, las fotos de los administradores.

1. Al guardar una foto, el bot responde a ella con el botón **«✍️ Describir
   esta foto»**, un enlace `t.me/<bot>?start=f<registro>` (el número de
   registro de la foto). Si Gemini dice que la foto no tiene que ver con
   COLFLUX, avisa que no la guardó.
2. Al tocarlo se abre el chat privado con el bot (`/start f<registro>`): el bot
   copia la foto del grupo (o la envía desde el bucket si el grupo no deja
   copiar), explica qué hacer y empieza las preguntas (ver «Preguntas por
   foto»), con los botones «No sé», «No aplica a esta foto», «📍 Compartir mi
   ubicación» y «✅ Terminar». Queda una **descripción en curso**
   (`descripciones_en_curso`: persona, chat privado, grupo, foto, desde, última,
   audio_en_espera).
3. Mientras esté en curso, cada texto o nota de voz de la persona se guarda en
   `observaciones_grupo` con el `chat_id` del grupo, `foto_message_id` de la foto,
   `canal = 'privado'` y `chat_origen` = el chat privado. En privado **solo se
   guardan descripciones**: sin contenido (emojis, risas) o lo que el filtro dice
   que no describe la foto o el sitio («hola», temas ajenos) no se guarda; el bot
   le dice a la persona por qué. El filtro también extrae los atributos.
4. Los audios se transcriben igual (Whisper), pero **la persona no ve la
   transcripción**: el bot solo le dice si el audio quedó guardado y sigue. Si la
   transcripción no es una descripción (o no responde la pregunta, o no se
   entendió nada), se borran el registro y el archivo, y se le pide repetirlo, sin
   mostrarle qué se entendió. **La revisión la hace el equipo**, en su chat
   privado con el bot y solo los administradores del grupo de ese registro:
   `/transcripcion <registro>` muestra la pregunta, lo que entendió Whisper y
   envía el audio; `/corregir <registro> <texto>` guarda lo que realmente se dijo
   (`transcripcion_corregida`, la de Whisper se conserva) y recalcula los
   atributos. El registro es la columna «registro» del Excel. A quien describe,
   esos comandos le responden que las transcripciones las revisa el equipo. En el
   bucket van en `telegram/<sitio>/audios/<día>/p<chat>-<mensaje>.ogg`.
5. La descripción termina con «✅ Terminar» (o `/terminar`), con «no», «nada
   más» o parecido en la pregunta final, o a las **12 horas** sin escribir
   (`MINUTOS_DESCRIPCION`); desde ahí el chat privado vuelve a ser el asistente
   de siempre. Si terminó antes de responder todo, el bot le dice cuántas
   preguntas le faltaron.
6. Los `message_id` de cada chat privado empiezan en 1: la unicidad es
   `(chat_id, message_id)` para lo del grupo y `(chat_origen, message_id)` para
   lo privado (dos índices únicos parciales; la restricción anterior se quita).
7. **Consulta solo con sesión:** `consultar_observaciones_grupo` exige la sesión
   de la plataforma, verificada con el backend. En Telegram (sin sesión) y en el
   chat web sin entrar, el asistente no muestra fotos ni descripciones de los
   grupos. El Excel no incluye los mensajes que no son descripciones y se llama
   `colflux-<sitio del grupo>-<fecha>.xlsx` (si mezcla varios grupos u otros
   datos, `colflux-datos-<fecha>.xlsx` como antes). Sus hojas: ver «Preguntas por
   foto».

## Preguntas por foto

Las preguntas son **todas las del «Instrumento de preguntas»** del proyecto
(versión 18-08-2026), en su orden y una por una (antes hubo una prueba con 6
preguntas adaptadas a la foto: quedan en el listado como inactivas).

- **Categoría 0, caracterización individual (10 preguntas, `alcance = persona`):**
  nombre, ubicación, género, pertenencia étnica, nivel de estudio, relación con
  el ecosistema, actividad económica, principales actividades, edad y tiempo
  que lleva viviendo en el lugar. Se hacen **una sola vez por persona**, la
  primera vez que describe una foto (si la deja a medias, sigue en la próxima
  foto), con el botón **«Prefiero no responder»**. Cualquier respuesta con
  letras o números vale (no pasa por el filtro de descripciones ni se le sacan
  atributos). Son datos personales: no van al chat, solo a la hoja
  «Caracterización» del Excel (que exige sesión).
- **Categorías 1 a 6 (31 preguntas, `alcance = foto`), en cada foto:**
  primeras aproximaciones a los ecosistemas (5), percepciones sobre el cambio
  (4), agua (5), suelo (5), biodiversidad vegetal (4), biodiversidad animal (5)
  y carbono (3), con «No sé» y «No aplica a esta foto». Redacción del
  instrumento; solo se quitaron las notas para el entrevistador (la de la
  Cat. 1 · P1 quedó como «el ecosistema de la foto»; «indagar por color,
  textura y olor» de la Cat. 4 · P1 quedó como pregunta).
- Al final, siempre, `ADICIONAL`: «¿Observas algo más en la imagen que no hayas
  descrito?».

1. **Listado editable:** `app/data/preguntas_foto.csv` (separado por `;`, UTF-8),
   columnas `codigo;orden;alcance;categoria;pregunta;origen_en_el_instrumento;activa`
   (`alcance`: `persona` = una vez por persona; `foto` o vacío = en cada foto).
   Se lee en cada mensaje: para cambiar una redacción, el orden o desactivar una
   pregunta (`activa = no`) basta editar el archivo, sin reiniciar. El `codigo`
   no se cambia una vez hay respuestas (es lo que las une en el Excel); para una
   pregunta nueva, un código nuevo. Otra ruta: variable `PREGUNTAS_FOTO`. Sin
   archivo, la descripción es libre, como antes.
2. **Una por una:** la pregunta que toca es la primera del listado que la
   persona no ha respondido sobre esa foto. Si deja la foto a medias y vuelve a
   tocar «✍️ Describir esta foto», sigue donde iba; si ya respondió todo, pasa a
   la pregunta final.
3. **Respuestas:** texto o nota de voz. «No sé» y «No aplica a esta foto» (botones,
   o escritos o dichos en el audio) responden la pregunta sin describir nada
   (`estado_respuesta` = `no_sabe` / `no_aplica`; si no, `respondida`). El filtro
   lee cada respuesta junto con su pregunta, así «alto» o «no» cuentan como
   respuesta; lo que no responde la pregunta no se guarda y se repite la pregunta.
   Un audio deja la pregunta en espera (`audio_en_espera`) hasta transcribirlo:
   si responde, sigue la siguiente; si no, se borra y se repite.
4. **Pregunta final** (`ADICIONAL`): «¿Observas algo más en la imagen que no
   hayas descrito?». Ahí se puede enviar uno o varios mensajes, con las reglas
   de siempre, hasta tocar «✅ Terminar».
5. Cada respuesta guarda `pregunta_codigo` y `pregunta_texto` (la redacción con
   que se preguntó), así que editar el listado no cambia lo ya respondido.
6. **Excel** (`exportar=true`), un libro con seis hojas (la caracterización
   solo en la suya):
   - **Respuestas:** una fila por respuesta: sitio, foto, fecha, autor, código,
     categoría y texto de la pregunta, estado (respondida / no sé / no aplica),
     respuesta, tipo, transcripción de Whisper y corregida, certeza, ubicación y
     los atributos del sitio que menciona. Lo anterior a las preguntas aparece
     con la pregunta vacía.
   - **Por persona y foto:** una fila por persona y foto, una columna por código
     de pregunta (varias respuestas a la misma se unen con « | »), `ADICIONAL`,
     cuántas respondió y cuántos «No sé» y «No aplica».
   - **Caracterización:** una fila por persona con sus respuestas de la
     categoría 0 («Prefiero no responder» cuando la saltó).
   - **Fotos:** cada foto con lo que ve la IA (descripción corta y atributos), el
     día de los datos, su ubicación y cuántas personas respondieron.
   - **Descripción IA:** la descripción de cada foto hecha por la IA con el mayor
     detalle posible, una columna por aspecto: general, paisaje y ecosistema,
     agua, nivel del agua, suelo, vegetación, fauna (los de las preguntas a las
     personas, para compararlas), cielo y luz, intervención humana, otros
     detalles y lo que la foto no permite saber; con el modelo y la fecha.
     Texto ajustado en celdas anchas.
   - **Preguntas:** el listado (también las inactivas), la pregunta final, las
     respondidas que ya no están en el listado y la versión del instrumento.

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
- Descripción detallada de la foto (`ia_detalle`, JSON por aspecto, con
  `ia_detalle_modelo` e `ia_detalle_el`): otra llamada a Gemini que pide
  describir solo lo que se ve, con el mayor detalle posible, marcando con
  «parece» lo que interpreta y sin nombrar especies que no sean inconfundibles.
  Se hace en segundo plano al guardar la foto (el botón no espera); las fotos
  que no la tienen (guardadas antes, o si Gemini falló) se completan cuando el
  bot lo pide al arrancar y cada día (`POST /grupos/fotos/detallar`, hasta 100
  por tanda, una tanda a la vez).
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
- Excel: ver «Preguntas por foto» (hojas Respuestas, Por persona y foto, Fotos,
  Descripción IA y Preguntas).
- `completar_atributos.py` calcula los atributos de lo guardado antes.

Fase siguiente (no incluida): consenso entre personas, contraste con la IA,
comparación con las mediciones del día de cada foto y estimación de un rango de
flujo a partir de los sitios medidos más parecidos.

## Piezas

| Archivo | Qué es |
| --- | --- |
| `app/domain/services/observaciones_grupo.py` | Tablas `grupos_observacion` y `observaciones_grupo` (se crean al primer uso), asociación a fotos, consulta y resumen |
| `app/api/grupos.py` | Rutas internas `/grupos/*` para el bot; exigen `X-Grupos-Token` = `GRUPOS_TOKEN` (vacío → 503) |
| `app/adapters/tools/observaciones_grupo_tool_provider.py` | Herramienta del chat y hojas del Excel |
| `app/domain/services/preguntas_foto.py` | Lee el listado de preguntas y dice cuál toca |
| `app/data/preguntas_foto.csv` | Listado editable de preguntas por foto |
| `app/domain/services/filtro_descripciones.py` | Filtro de descripciones (con la pregunta, si responde a una) |
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

Preguntas por foto, en el laboratorio: tocar «✍️ Describir esta foto» → el bot
muestra la foto, la introducción y «Pregunta 1 de 6»; responder por texto, con
«No sé», con «No aplica a esta foto» y con una nota de voz → cada vez sigue la
siguiente; escribir «hola» → no se guarda y repite la pregunta; tras la 6, la
pregunta final; «no» → termina. Volver a tocar el botón de la misma foto → «Ya
respondiste las 6 preguntas» y la pregunta final. En el chat web con sesión:
«dame en Excel las observaciones de Paramox» → libro con las cinco hojas; en
«Descripción IA», las fotos anteriores aparecen después de reiniciar el bot (o
«Pendiente» mientras tanto).
