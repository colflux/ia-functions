from fastapi import APIRouter

from app.api.schemas import ChatRequest, ChatResponse, IngestRequest, IngestResponse
from app.rag.pipeline import answer_question, ingest_document

router = APIRouter()


@router.post("/ingest", response_model=IngestResponse)
def ingest(payload: IngestRequest) -> IngestResponse:
    count = ingest_document(payload.source, payload.text)
    return IngestResponse(chunks_indexed=count)


@router.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    result = answer_question(payload.message)
    return ChatResponse(**result)
