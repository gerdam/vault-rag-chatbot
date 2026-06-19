"""
main.py — FastAPI Backend für den Vault-RAG-Chatbot.
Start: uvicorn main:app --reload
"""

import os

import anthropic
import chromadb
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_data")
N_RESULTS = int(os.getenv("N_RESULTS", "5"))

app = FastAPI(title="Vault RAG Chatbot", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
claude = anthropic.Anthropic()


def get_collection():
    try:
        return chroma_client.get_collection("vault")
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Vault nicht indexiert. Zuerst 'python index.py' ausführen.",
        )


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    antwort: str
    quellen: list[str]


@app.get("/health")
async def health():
    try:
        col = get_collection()
        return {"status": "ok", "chunks": col.count()}
    except HTTPException:
        return {"status": "nicht indexiert", "chunks": 0}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    col = get_collection()

    # 1. Relevante Chunks aus ChromaDB abrufen
    results = col.query(query_texts=[req.message], n_results=N_RESULTS)
    chunks: list[str] = results["documents"][0]
    metas: list[dict] = results["metadatas"][0]

    if not chunks:
        return ChatResponse(
            antwort="Keine relevanten Notizen gefunden.",
            quellen=[],
        )

    # 2. Kontext für Claude aufbauen
    kontext = "\n\n---\n\n".join(chunks)

    # 3. Antwort von Claude generieren
    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=(
            "Du bist ein Assistent, der Fragen ausschließlich auf Basis "
            "der folgenden Notizen aus einem persönlichen Wissens-Vault beantwortet. "
            "Wenn die Antwort nicht in den Notizen steht, sage das klar und ehrlich. "
            "Antworte auf Deutsch.\n\n"
            f"NOTIZEN:\n{kontext}"
        ),
        messages=[{"role": "user", "content": req.message}],
    )

    return ChatResponse(
        antwort=response.content[0].text,
        quellen=list({m["datei"] for m in metas}),  # dedupliziert
    )
