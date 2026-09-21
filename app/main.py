from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.bootstrap.container import get_rag_service
from app.db.session import init_schema

app = FastAPI(title="colflux IA engine")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.on_event("startup")
def on_startup() -> None:
    init_schema()
    reindexados = get_rag_service().reindexar_pendientes()
    if reindexados:
        print(f"[schema] {reindexados} fragmentos reindexados con el modelo actual", flush=True)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
