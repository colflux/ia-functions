from app.config import settings
from app.llm.factory import get_llm_provider
from app.rag.chunking import chunk_text
from app.rag.embeddings import embed_query, embed_texts
from app.rag.vectorstore import insert_chunks, search

SYSTEM_PROMPT = (
    "Eres el asistente de colflux, experto en datos de carbono y huella de "
    "emisiones. Si se te da contexto recuperado de la base de conocimiento, "
    "basa tu respuesta en el y cita que se uso. Si no hay contexto (por "
    "ejemplo, saludos o preguntas generales), conversa con naturalidad y "
    "responde con tu conocimiento general, aclarando que no proviene de "
    "los documentos indexados cuando la pregunta sea sobre datos de carbono."
)


def ingest_document(source: str, text: str) -> int:
    chunks = chunk_text(text)
    if not chunks:
        return 0
    embeddings = embed_texts(chunks)
    return insert_chunks(source, chunks, embeddings)


def answer_question(question: str, top_k: int | None = None) -> dict:
    query_embedding = embed_query(question)
    matches = search(query_embedding, top_k or settings.retrieval_top_k)
    relevant = [m for m in matches if m["score"] >= settings.retrieval_min_score]

    if relevant:
        context = "\n\n".join(f"[{m['source']}] {m['content']}" for m in relevant)
        prompt = f"Contexto recuperado:\n{context}\n\nPregunta: {question}"
    else:
        prompt = f"No hay contexto relevante en la base de conocimiento.\n\nPregunta: {question}"

    llm = get_llm_provider()
    answer = llm.generate(prompt, system=SYSTEM_PROMPT)

    return {"answer": answer, "sources": relevant}
