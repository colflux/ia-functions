from fastapi import FastAPI

from app.api.routes import router
from app.db.session import init_schema

app = FastAPI(title="colflux IA engine")
app.include_router(router)


@app.on_event("startup")
def on_startup() -> None:
    init_schema()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
