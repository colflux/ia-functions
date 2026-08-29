from app.config import settings
from app.llm.factory import get_llm_provider
from app.rag.chunking import chunk_text
from app.rag.embeddings import embed_query, embed_texts
from app.rag.vectorstore import insert_chunks, search

SYSTEM_PROMPT = (
    "Eres un asistente experto en datos de carbono y huella de emisiones. "
    "Responde usando unicamente el contexto proporcionado. "
    "Si el contexto no contiene la respuesta, dilo explicitamente."
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

    context = "\n\n".join(f"[{m['source']}] {m['content']}" for m in matches)
    prompt = f"Contexto:\n{context}\n\nPregunta: {question}"

    llm = get_llm_provider()
    answer = llm.generate(prompt, system=SYSTEM_PROMPT)

    return {"answer": answer, "sources": matches}
