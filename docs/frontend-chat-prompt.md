# Prompt: chat React para el motor de IA de colflux

Prompt listo para pasarle a Claude Code (u otro asistente) en el repo/carpeta
donde se cree el frontend del chat que consume esta API.

```
Quiero crear una interfaz de chat en React (Vite + TypeScript) que consuma
una API RAG ya existente. Genera el proyecto completo.

## Backend (ya existe, no lo toques)
- Base URL: http://localhost:8001 (configurable vía variable de entorno
  VITE_API_URL)
- POST /chat
  Request:  { "message": string }
  Response: { "answer": string, "sources": [{ "source": string, "content": string, "score": number }] }
- POST /ingest (opcional, para subir texto a la base de conocimiento)
  Request:  { "source": string, "text": string }
  Response: { "chunks_indexed": number }
- No requiere autenticación por ahora. CORS ya está habilitado en el backend.

## Requisitos del frontend
- React + TypeScript + Vite.
- Una sola pantalla de chat estilo mensajería:
  - Historial de mensajes (usuario a la derecha, asistente a la izquierda).
  - Input de texto + botón enviar (y enviar con Enter).
  - Estado de "escribiendo..." mientras espera la respuesta de POST /chat.
  - Debajo de cada respuesta del asistente, mostrar las fuentes
    (sources) usadas, colapsadas/expandibles, con el score de similitud.
  - Manejo de errores de red (mostrar un mensaje si la API falla).
- Guarda el historial de la conversación en memoria (state de React),
  no hace falta persistencia todavía.
- Usa fetch nativo o axios, lo que prefieras, pero mantenlo simple.
- Estilo limpio y minimalista (puedes usar CSS plano o Tailwind, tu elección).
- No implementes streaming de tokens todavía, el backend responde
  la respuesta completa de una sola vez.

Al terminar, dame instrucciones para correrlo en local (npm run dev)
apuntando a http://localhost:8001.
```

## Notas

- El backend ya tiene CORS habilitado (`app/main.py`), así que no debería
  haber bloqueos al llamarlo desde `localhost:5173` (puerto típico de Vite)
  u otro origen durante desarrollo local.
- Si además quieres subir documentos de carbono desde la misma UI (no solo
  por `curl`), agrega al prompt un formulario simple para `/ingest`.
- Antes de conectar el frontend, levanta este backend con
  `docker compose up -d db api` (ver [README](../README.md)).
