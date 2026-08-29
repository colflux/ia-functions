from pydantic import BaseModel


class IngestRequest(BaseModel):
    source: str
    text: str


class IngestResponse(BaseModel):
    chunks_indexed: int


class ChatRequest(BaseModel):
    message: str


class Source(BaseModel):
    source: str
    content: str
    score: float


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
