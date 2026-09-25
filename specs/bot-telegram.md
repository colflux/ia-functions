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
- Chats privados: responde todo. Grupos: solo si lo mencionan (`@bot`) o le responden
  a un mensaje suyo.
- Las respuestas van en texto plano. Si pasan de 4.096 caracteres, se parten por
  líneas.
- Las fuentes que se pueden abrir (páginas y PDF de la wiki) se agregan como enlaces.
  Los archivos subidos no se enlazan porque su descarga exige sesión.
- Fotos, documentos, audios o ubicaciones: responde que por ahora solo atiende texto
  y da el enlace a la plataforma.
- Excel: si la respuesta trae `descargas`, pide el enlace a `/descargas/excel`, baja
  el archivo y lo envía con el número de filas.
- Los mensajes de un mismo chat se responden en orden, y hasta 4 chats se atienden a
  la vez.

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
| `docker-compose.yml`, `.env.example` | Servicio `telegram` y sus variables |

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
