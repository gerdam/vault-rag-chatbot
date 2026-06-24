# Antwort-Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claudes Antwort tokenweise per SSE ins React-UI streamen, während sie generiert wird.

**Architecture:** Neuer additiver Endpoint `/chat/stream` (`async def`) neben dem unveränderten `/chat`. Backend nutzt `AsyncAnthropic` + `async for` über `text_stream` und lagert den blockierenden ChromaDB-Call per `run_in_threadpool` aus. Stream transportiert typisierte SSE-Events (`sources` → `token`* → `done`/`error`). Frontend liest den Stream via `fetch` + `ReadableStream` und lässt die Antwort-Blase live wachsen.

**Tech Stack:** FastAPI (StreamingResponse, run_in_threadpool), anthropic AsyncAnthropic, ChromaDB, React/TypeScript (fetch + ReadableStream + TextDecoder), pytest + TestClient.

---

## File Structure

- `backend/main.py` — **modify**: SSE-Helper, `get_async_claude`-Dependency, `/chat/stream`-Endpoint. `/chat`, `/health`, bestehende Dependencies unangetastet.
- `backend/tests/test_chat.py` — **modify**: async-Fakes + 1 neuer Streaming-Test. Bestehende 3 Tests unangetastet.
- `frontend/src/App.tsx` — **modify**: `frageSenden` ruft neue `frageSendenStream`-Logik; SSE-Parsing; live wachsende Blase.

---

## Task 1: Backend — `/chat/stream`-Endpoint (TDD)

**Files:**
- Modify: `backend/main.py`
- Test: `backend/tests/test_chat.py`

- [ ] **Step 1: Async-Fakes + failing test schreiben**

In `backend/tests/test_chat.py` am Ende anhängen (nach `test_chat_ohne_index_gibt_503`):

```python
# --- Fakes für den async Streaming-Endpoint -----------------------------
class _FakeAsyncStream:
    """Simuliert claude.messages.stream(...) als async context manager."""

    def __init__(self, texte):
        self._texte = texte

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def text_stream(self):
        async def gen():
            for t in self._texte:
                yield t
        return gen()


class _FakeAsyncMessages:
    def __init__(self, texte):
        self._texte = texte

    def stream(self, **kwargs):
        # WICHTIG: synchroner Aufruf, der einen async-CM zurückgibt (wie das echte SDK)
        return _FakeAsyncStream(self._texte)


class FakeAsyncClaude:
    def __init__(self, texte):
        self.messages = _FakeAsyncMessages(texte)


def _parse_sse(text):
    """Parst rohen SSE-Text in eine Liste von (event, data)-Tupeln."""
    import json
    events = []
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        event_name, data_json = None, None
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_json = line[len("data:"):].strip()
        events.append((event_name, json.loads(data_json)))
    return events


def test_chat_stream_sendet_sources_dann_tokens_dann_done(client):
    docs = ["Rust ist speichersicher."]
    metas = [{"datei": "rust.md"}, {"datei": "rust.md"}]  # Duplikat -> dedupliziert
    texte = ["Laut den Notizen ", "ist Rust speichersicher."]

    main.app.dependency_overrides[main.get_collection] = lambda: FakeCollection(docs, metas)
    main.app.dependency_overrides[main.get_async_claude] = lambda: FakeAsyncClaude(texte)

    r = client.post("/chat/stream", json={"message": "Ist Rust sicher?"})

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(r.text)
    assert events[0] == ("sources", {"quellen": ["rust.md"]})
    token_texte = [d["text"] for (e, d) in events if e == "token"]
    assert "".join(token_texte) == "Laut den Notizen ist Rust speichersicher."
    assert events[-1] == ("done", {})
```

- [ ] **Step 2: Test ausführen, Fehlschlag bestätigen**

Run: `cd backend && .venv\Scripts\python.exe -m pytest tests/test_chat.py::test_chat_stream_sendet_sources_dann_tokens_dann_done -v`
Expected: FAIL — `AttributeError: module 'main' has no attribute 'get_async_claude'` (bzw. 404 auf `/chat/stream`).

- [ ] **Step 3: Imports in `main.py` ergänzen**

In `backend/main.py` die Import-Sektion erweitern. Bestehende Zeilen:

```python
import os
from functools import lru_cache

import anthropic
import chromadb
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
```

ersetzen durch:

```python
import json
import os
from functools import lru_cache

import anthropic
import chromadb
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
```

- [ ] **Step 4: `get_async_claude`-Dependency hinzufügen**

In `backend/main.py` direkt nach `get_claude` (nach Zeile `return anthropic.Anthropic()`) einfügen:

```python
@lru_cache
def get_async_claude() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic()
```

- [ ] **Step 5: SSE-Helper + `/chat/stream`-Endpoint hinzufügen**

In `backend/main.py` ans Dateiende anhängen:

```python
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
```

- [ ] **Step 6: Test ausführen, Erfolg bestätigen**

Run: `cd backend && .venv\Scripts\python.exe -m pytest tests/test_chat.py::test_chat_stream_sendet_sources_dann_tokens_dann_done -v`
Expected: PASS

- [ ] **Step 7: Alle Tests ausführen (Regression)**

Run: `cd backend && .venv\Scripts\python.exe -m pytest -q`
Expected: `4 passed` (3 alte + 1 neuer).

- [ ] **Step 8: Commit**

```bash
git add backend/main.py backend/tests/test_chat.py
git commit -m "feat(backend): SSE-Streaming-Endpoint /chat/stream (async)"
```

---

## Task 2: Frontend — Stream konsumieren und live anzeigen

**Files:**
- Modify: `frontend/src/App.tsx`

Kein Frontend-Test-Runner im Projekt → Verifikation manuell im Browser (Task 3).

- [ ] **Step 1: `frageSenden` durch Streaming-Variante ersetzen**

In `frontend/src/App.tsx` die komplette Funktion `frageSenden` (Zeilen 33–66) ersetzen durch:

```tsx
  async function frageSenden() {
    const frage = eingabe.trim();
    if (!frage || laedt) return;

    // User-Nachricht + leere Antwort-Blase (waechst gleich) anlegen.
    setVerlauf((v) => [
      ...v,
      { rolle: "frage", text: frage },
      { rolle: "antwort", text: "", quellen: [] },
    ]);
    setEingabe("");
    setLaedt(true);
    setFehler(null);

    // Haengt Text an die LETZTE Nachricht (die Antwort-Blase) an.
    const anAntwortAnhaengen = (text: string) =>
      setVerlauf((v) => {
        const kopie = [...v];
        const letzte = kopie[kopie.length - 1];
        kopie[kopie.length - 1] = { ...letzte, text: letzte.text + text };
        return kopie;
      });

    const setzeQuellen = (quellen: string[]) =>
      setVerlauf((v) => {
        const kopie = [...v];
        const letzte = kopie[kopie.length - 1];
        kopie[kopie.length - 1] = { ...letzte, quellen };
        return kopie;
      });

    try {
      const res = await fetch(`${API_URL}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: frage }),
      });

      if (!res.ok || !res.body) {
        throw new Error(`Server antwortete mit ${res.status}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let puffer = "";

      // Stream lesen, bis er endet.
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        puffer += decoder.decode(value, { stream: true });

        // Vollstaendige SSE-Bloecke sind durch eine Leerzeile getrennt.
        const bloecke = puffer.split("\n\n");
        puffer = bloecke.pop() ?? ""; // letzter (evtl. unvollstaendiger) Block bleibt im Puffer

        for (const block of bloecke) {
          if (!block.trim()) continue;
          let eventName = "";
          let dataJson = "";
          for (const zeile of block.split("\n")) {
            if (zeile.startsWith("event:")) eventName = zeile.slice(6).trim();
            else if (zeile.startsWith("data:")) dataJson = zeile.slice(5).trim();
          }
          const daten = dataJson ? JSON.parse(dataJson) : {};

          if (eventName === "sources") setzeQuellen(daten.quellen ?? []);
          else if (eventName === "token") anAntwortAnhaengen(daten.text ?? "");
          else if (eventName === "error") throw new Error(daten.detail ?? "Stream-Fehler");
          // "done" -> Schleife endet ohnehin am Stream-Ende
        }
      }
    } catch (e) {
      setFehler(e instanceof Error ? e.message : "Unbekannter Fehler");
    } finally {
      setLaedt(false);
    }
  }
```

- [ ] **Step 2: Lint prüfen**

Run: `cd frontend && npm run lint`
Expected: keine neuen Fehler in `App.tsx`.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(frontend): Antwort-Stream via fetch+ReadableStream live anzeigen"
```

---

## Task 3: End-to-End-Verifikation (manuell)

**Files:** keine Änderung — nur Ausführung.

- [ ] **Step 1: Backend starten**

```bash
cd backend
.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

- [ ] **Step 2: Stream per curl prüfen (zweites Terminal)**

Run:
```bash
curl -N -X POST http://127.0.0.1:8000/chat/stream -H "Content-Type: application/json" -d "{\"message\":\"Was ist Rust?\"}"
```
Expected: zuerst `event: sources` mit `data: {"quellen": [...]}`, dann viele `event: token`-Zeilen die nach und nach erscheinen (nicht alles auf einmal), zuletzt `event: done`.

- [ ] **Step 3: Frontend starten und im Browser testen**

```bash
cd frontend && npm run dev
```
Im Browser (http://localhost:5173) eine Frage stellen. Expected: Die Antwort-Blase **wächst sichtbar Wort für Wort**, Quellen erscheinen unter der fertigen Antwort.

- [ ] **Step 4: Abschluss-Commit (falls noch uncommittete Reste)**

```bash
git status   # erwartet: clean
```

---

## Self-Review

**Spec coverage:** async-Backend (Task 1, Steps 4–5) ✓ · typisierte SSE-Events sources/token/done/error (Task 1, Step 5) ✓ · run_in_threadpool-Lern-Highlight (Task 1, Step 5) ✓ · fetch+ReadableStream-Frontend (Task 2) ✓ · bestehender `/chat` unangetastet ✓ · neuer Streaming-Test, alte 3 bleiben (Task 1, Steps 1+7) ✓ · Error-Event (Task 1, Step 5; Task 2, Step 1) ✓ · 503 über get_collection bleibt (Endpoint hängt an get_collection wie /chat) ✓.

**Placeholder scan:** keine TBD/TODO; alle Code-Schritte vollständig.

**Type consistency:** `get_async_claude`, `_sse`, `event_stream`, Event-Namen `sources`/`token`/`done`/`error`, Datenfelder `quellen`/`text`/`detail` durchgängig identisch in Backend, Test und Frontend.
