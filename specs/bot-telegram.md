# Bot de Telegram del asistente

## Introducción

Un bot de Telegram que responde igual que el chat de la plataforma: cada mensaje va al
mismo `/chat` del asistente. Por eso usa los mismos datos, diccionario, wiki y reglas.
Si la respuesta trae datos en Excel, envía el archivo en el chat.

## Contexto

Estaba pendiente ofrecer el asistente por un canal de mensajería. Se eligió Telegram
porque es más fácil: la API de bots es gratuita, el bot se crea con @BotFather y no
necesita aprobación ni número de teléfono. Decisiones:

- **Primera versión: consultar y Excel**, sin iniciar sesión, como un visitante de la
  web. Subir archivos y registrar mediciones queda para una versión con cuenta
  vinculada.
- **Abierto a cualquiera, con un tope de mensajes** por persona, para no agotar la
  cuota gratuita de los proveedores de IA.

## Qué hace

### Funcionamiento

- Servicio `telegram` en el docker-compose, con la misma imagen del asistente
  (`python -m telegram_bot`). Le habla al asistente por la red interna
  (`http://api:8000`).
- **Consulta periódica en vez de webhook**: el webhook exige HTTPS con dominio, que
  producción aún no tiene. Con la consulta periódica el bot solo hace conexiones de
  salida, sin puertos abiertos.
- Sin `TELEGRAM_BOT_TOKEN` el servicio termina sin hacer nada (`restart: on-failure`
  no lo relanza).
- **Un bot por entorno**: Telegram entrega cada mensaje a un solo lector, así que el
  laboratorio y producción no pueden compartir token.

### Conversación

- Cada persona es un usuario del asistente (`telegram-<id>`): el asistente conserva
  su historial igual que en la web.
- `/start` y `/ayuda` muestran qué se puede preguntar y dónde subir archivos.
- Chats privados: responde todo, salvo mientras la persona describe una foto de un
  grupo de observación (ver abajo). Grupos: solo si lo mencionan (`@bot`) o le
  responden a un mensaje suyo.
- Las respuestas van en texto plano. Si pasan de 4.096 caracteres, se parten por
  líneas.
- Las fuentes que se pueden abrir (páginas y PDF de la wiki) se agregan como enlaces.
  Los archivos subidos no se enlazan porque su descarga exige sesión.
- Fotos, documentos, audios o ubicaciones fuera de una descripción: responde que por
  ahora solo atiende texto y da el enlace a la plataforma. Una ubicación compartida
  en privado se guarda para las descripciones (ver `grupos-observacion.md`).
- Excel: si la respuesta trae `descargas`, pide el enlace a `/descargas/excel`, baja
  el archivo y lo envía con el número de filas.
- Los mensajes de un mismo chat se responden en orden, y hasta 4 chats se atienden a
  la vez.

### Grupos de observación y descripciones en privado

El detalle está en `specs/grupos-observacion.md`. Lo que cambia en el bot:

- En un grupo registrado con `/observacion <sitio>` (comandos solo para
  administradores) el bot guarda en silencio las fotos que envía el equipo y pone
  bajo cada una el botón «✍️ Describir esta foto». Lo demás que se escriba en el
  grupo no se guarda.
- El botón abre el chat privado (`/start f<registro>`). Desde ahí, y hasta
  «✅ Terminar» (`/terminar`) o 12 horas sin escribir, lo que la persona escriba o
  grabe en privado es su respuesta a las preguntas del instrumento, una por una,
  y no una pregunta al asistente. Después el chat vuelve a ser el asistente.
- Orden en el chat privado: ubicación compartida → `/ubicacion` → `/start f<registro>`
  → «✅ Terminar» → `/transcripcion` y `/corregir` → respuesta a la descripción en
  curso → asistente.
- Las notas de voz se transcriben con Whisper en el mismo servicio. Quien describe
  no ve la transcripción; el equipo la revisa con `/transcripcion <registro>` y
  `/corregir <registro> <texto>` en su chat privado con el bot (solo administradores
  del grupo de ese registro).
- Una vez al arrancar y luego cada día, el bot pide a la api retomar los audios sin
  transcribir, borrar los audios vencidos y completar la descripción detallada de
  las fotos que no la tengan.
- Lo de los grupos no se consulta desde Telegram: el asistente solo lo muestra con
  la sesión iniciada en la plataforma.

### Límites y errores

- `TELEGRAM_MENSAJES_POR_HORA` (20 por defecto) por persona, en memoria: se reinicia
  si se reinicia el servicio.
- Si el asistente no responde, avisa que no está disponible. Si Telegram falla,
  reintenta a los 10 s.
- El token no se escribe en los registros: se silencia el registro de peticiones de
  httpx, que incluye la URL con el token.

### Archivos

| Archivo | Qué hace |
|---------|----------|
| `telegram_bot/__main__.py` | Arranque y variables |
| `telegram_bot/bot.py` | Atención de mensajes, límite, fuentes y Excel |
| `telegram_bot/telegram.py` | Cliente mínimo de la API de bots |
| `telegram_bot/asistente.py` | Cliente de `/chat` y `/descargas/excel` |
| `telegram_bot/registro_grupos.py` | Grupos de observación, descripciones y preguntas en privado |
| `telegram_bot/grupos.py` | Cliente de las rutas internas `/grupos` (con `GRUPOS_TOKEN`) |
| `telegram_bot/transcriptor.py` | Transcripción de notas de voz con Whisper |
| `docker-compose.yml`, `.env.example` | Servicio `telegram` y sus variables (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_MENSAJES_POR_HORA`, `GRUPOS_TOKEN`, `WHISPER_MODELO`, `WHISPER_HILOS`) |

## Plan por fases

1. **Consulta y Excel** (este cambio).
2. **Cuenta vinculada**: código de un solo uso generado en el perfil de la plataforma,
   para subir fotos y archivos (con la ubicación que Telegram comparte) y registrar
   mediciones con nivel reportador.
3. **Webhook** cuando haya HTTPS, si el volumen lo justifica.

## Verificación

- Pruebas locales con Telegram y el asistente simulados: bienvenida, respuesta con
  fuentes de la wiki y Excel, tope por hora, foto rechazada, grupo sin mención ignorado
  y con mención respondido, textos largos partidos.
- Laboratorio con el bot de pruebas: preguntas de datos, diccionario y wiki; pedir el
  Excel; foto; tope.

## Al desplegar

- Crear el bot de producción con @BotFather y guardar `TELEGRAM_BOT_TOKEN` en el
  `.env` sin mostrarlo en pantalla.
- `docker compose up -d --build telegram`.
