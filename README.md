# colflux · IA engine

Motor de IA de colflux: una API con RAG sobre datos de carbono, pensada para
integrarse con un chat externo (web, WhatsApp, Slack, etc.).

## Arquitectura

- **API**: FastAPI (`app/main.py`), expone `POST /ingest` y `POST /chat`.
- **Vector store**: Postgres + [pgvector](https://github.com/pgvector/pgvector).
- **Embeddings**: `sentence-transformers` (modelo local, gratis, offline).
- **LLM**: intercambiable vía la variable `LLM_PROVIDER`. Implementaciones
  disponibles en `app/llm/`:
  - `groq` (default, free tier, recomendado para empezar sin costo)
  - `gemini`
  - `ollama` (modelo local en Docker, 100% gratis y offline)
  - `anthropic`

  Cambiar de proveedor no requiere tocar código, solo `LLM_PROVIDER` y su
  API key correspondiente en `.env`.

## Requisitos

- Docker y Docker Compose

## Uso

```bash
cp .env.example .env
# Completa al menos GROQ_API_KEY (https://console.groq.com) o cambia
# LLM_PROVIDER a otro proveedor.

docker compose up -d db api
```

La API queda disponible en `http://localhost:8001`.

Si quieres usar Ollama en vez de una API externa:

```bash
docker compose --profile ollama up -d db api ollama
# Descarga el modelo dentro del contenedor:
docker compose exec ollama ollama pull llama3.1
# Y en .env: LLM_PROVIDER=ollama
```

### Endpoints

- `GET /health` — chequeo de salud.
- `POST /ingest` — indexa un documento.
  ```json
  {"source": "reporte-2024.pdf", "text": "..."}
  ```
- `POST /chat` — hace una pregunta contra el conocimiento indexado.
  ```json
  {"message": "¿Cuál es el factor de emisión de la red eléctrica?"}
  ```

## Desarrollo local

El servicio `api` monta `./app` como volumen, así que los cambios de código
se reflejan sin reconstruir la imagen (reinicia el contenedor para que
`uvicorn` los recargue: `docker compose restart api`).
