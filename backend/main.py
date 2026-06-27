"""
main.py — FastAPI Backend für den Vault-RAG-Chatbot.
Start: uvicorn main:app --reload
"""

import json
import os
import sqlite3
from functools import lru_cache

import anthropic
import chromadb
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import history

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


# --- Dependencies --------------------------------------------------------
# lru_cache sorgt dafür, dass jeder Client genau EINMAL erzeugt wird
# (beim ersten Request) und danach dieselbe Instanz zurückkommt — kein
# Neuaufbau pro Request. Gleichzeitig sind beide jetzt injizierbar und im
# Test über app.dependency_overrides austauschbar.

@lru_cache
def get_chroma_client() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=CHROMA_PATH)


@lru_cache
def get_claude() -> anthropic.Anthropic:
    return anthropic.Anthropic()


@lru_cache
def get_async_claude() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic()


# Verschachtelte Dependency: hängt selbst von get_chroma_client ab.
# Wirft sie HTTPException, läuft der Endpoint gar nicht erst an.
def get_collection(client: chromadb.ClientAPI = Depends(get_chroma_client)):
    try:
        return client.get_collection("vault")
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Vault nicht indexiert. Zuerst 'python index.py' ausführen.",
        )


class ChatRequest(BaseModel):
    message: str
    session_id: str


class ChatResponse(BaseModel):
    antwort: str
    quellen: list[str]


# /health darf NICHT von get_collection abhängen: diese Dependency wirft
# 503, wenn nicht indexiert — die Exception würde den Endpoint überspringen
# und /health könnte den Status "nicht indexiert" nie melden. Deshalb hängt
# /health direkt am Client und prüft selbst.
# def (kein async): col.count() ist synchron → läuft im Threadpool.
@app.get("/health")
def health(client: chromadb.ClientAPI = Depends(get_chroma_client)):
    try:
        col = client.get_collection("vault")
        return {"status": "ok", "chunks": col.count()}
    except Exception:
        return {"status": "nicht indexiert", "chunks": 0}


# def statt async def: col.query() und claude.messages.create() sind beide
# synchron/blockierend. In async def würden sie die Event-Loop blockieren;
# als def führt FastAPI den Endpoint im Threadpool aus → Loop bleibt frei.
@app.post("/chat", response_model=ChatResponse)
def chat(
    req: ChatRequest,
    col=Depends(get_collection),
    claude: anthropic.Anthropic = Depends(get_claude),
    db: sqlite3.Connection = Depends(history.get_history_db),
):
    verlauf = history.load_history(db, req.session_id, history.HISTORY_MAX_TURNS * 2)
    retrieval_query = history.build_retrieval_query(verlauf, req.message)

    # 1. Relevante Chunks aus ChromaDB abrufen
    results = col.query(query_texts=[retrieval_query], n_results=N_RESULTS)
    chunks: list[str] = results["documents"][0]
    metas: list[dict] = results["metadatas"][0]

    if not chunks:
        antwort = "Keine relevanten Notizen gefunden."
        history.save_message(db, req.session_id, "user", req.message)
        history.save_message(db, req.session_id, "assistant", antwort)
        return ChatResponse(antwort=antwort, quellen=[])

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
        messages=history.build_messages(verlauf, req.message),
    )
    antwort = response.content[0].text
    history.save_message(db, req.session_id, "user", req.message)
    history.save_message(db, req.session_id, "assistant", antwort)

    return ChatResponse(
        antwort=antwort,
        quellen=list({m["datei"] for m in metas}),  # dedupliziert
    )


# --- Streaming-Endpoint --------------------------------------------------
def _sse(event: str, data: dict) -> str:
    """Formatiert ein Server-Sent Event: 'event:'-Zeile + 'data:'-JSON + Leerzeile."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# async def: Anthropic-Stream wird mit 'await'/'async for' konsumiert. Der
# blockierende ChromaDB-Call (col.query) würde die Event-Loop einfrieren —
# deshalb läuft er über run_in_threadpool im Threadpool.
@app.post("/chat/stream")
async def chat_stream(
    req: ChatRequest,
    col=Depends(get_collection),
    claude: anthropic.AsyncAnthropic = Depends(get_async_claude),
):
    async def event_stream():
        # 1. Retrieval — sync-Lib bewusst in den Threadpool ausgelagert
        results = await run_in_threadpool(
            col.query, query_texts=[req.message], n_results=N_RESULTS
        )
        chunks: list[str] = results["documents"][0]
        metas: list[dict] = results["metadatas"][0]

        if not chunks:
            yield _sse("sources", {"quellen": []})
            yield _sse("token", {"text": "Keine relevanten Notizen gefunden."})
            yield _sse("done", {})
            return

        # 2. Quellen stehen sofort fest -> als erstes Event senden
        quellen = list({m["datei"] for m in metas})
        yield _sse("sources", {"quellen": quellen})

        kontext = "\n\n---\n\n".join(chunks)

        # 3. Tokens von Claude streamen
        try:
            async with claude.messages.stream(
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
            ) as stream:
                async for text in stream.text_stream:
                    yield _sse("token", {"text": text})
        except Exception as e:  # noqa: BLE001 — Fehler an den Client durchreichen
            yield _sse("error", {"detail": str(e)})
            return

        yield _sse("done", {})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
