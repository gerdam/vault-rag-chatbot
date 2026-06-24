# Design: Antwort-Streaming für den Vault-RAG-Chatbot

> Erstellt: 2026-06-24 — Spec für SSE-basiertes Token-Streaming.
> Lernkontext: full-stack-study, vertieft `async` (FastAPI + AsyncAnthropic) praktisch.

## Ziel

Claudes Antwort soll **tokenweise im UI erscheinen, während sie generiert wird** —
statt erst nach der vollständigen Antwort. Verbindet `async`/`await` mit
Server-Sent Events (SSE).

## Kern-Entscheidungen

1. **Backend-Streaming: async** — `async def`-Endpoint + `AsyncAnthropic` +
   `async with client.messages.stream()` + `async for`.
2. **Stream-Protokoll: typisierte SSE-Events** — `sources` → `token`(s) → `done`.
3. **Frontend-Konsum: fetch + ReadableStream** — POST bleibt erhalten, SSE-Events
   werden manuell geparst.

## Architektur — additiv, nicht ersetzend

`/chat` bleibt **unverändert** (inkl. der 3 grünen Tests). Wir fügen `/chat/stream`
daneben hinzu. Vorteile: sync- vs. async-Variante direkt vergleichbar, trivialer
Rollback, bestehende Tests bleiben gültig.

## Backend (`backend/main.py`)

- **Neue Dependency** `get_async_claude()` → `anthropic.AsyncAnthropic()`, mit `@lru_cache`
  (gleiches Muster wie `get_claude` / `get_chroma_client`).
- **Neuer Endpoint** `async def chat_stream(req, col, claude)` → gibt
  `StreamingResponse(generator(), media_type="text/event-stream")` zurück.
- **Async-Generator** sendet drei Event-Typen im SSE-Format
  (`event: <typ>\ndata: <json>\n\n`):
  - `sources` — einmalig zuerst: `{"quellen": [...]}` (deduplizierte Dateinamen)
  - `token` — viele: `{"text": "..."}` pro Textfragment
  - `done` — einmalig am Ende: `{}`
- Token-Quelle: `async with claude.messages.stream(...) as stream:` +
  `async for text in stream.text_stream:`.

### ⚠️ Lern-Highlight: die async-Falle im selben Endpoint

Der Endpoint ist `async def`. `col.query()` (ChromaDB) ist **synchron/blockierend** —
ein Direktaufruf würde die Event-Loop einfrieren. Lösung: Retrieval in den Threadpool
auslagern.

```python
from fastapi.concurrency import run_in_threadpool

results = await run_in_threadpool(
    col.query, query_texts=[req.message], n_results=N_RESULTS
)
```

Damit werden **beide** async-Werkzeuge in einem Endpoint geübt:
- `await` auf eine echte async-Library (Anthropic),
- Threadpool-Auslagern einer sync-Library (ChromaDB).

## Frontend (`frontend/src/App.tsx`)

- Neue Funktion `frageSendenStream()` ersetzt den Aufruf in `frageSenden()`:
  - `fetch` POST an `/chat/stream` (Body wie bisher: `{ message }`).
  - `const reader = res.body.getReader()` + `TextDecoder`.
  - Puffer an `\n\n` splitten; je Block `event:`- und `data:`-Zeile trennen.
  - `sources` → Quellen für die kommende Antwort-Blase vormerken.
  - `token` → Text an die **letzte** Antwort-Nachricht im Verlauf anhängen
    (live wachsende Blase).
  - `done` → Stream-Ende, `laedt` zurücksetzen.
- Beim ersten `token` (oder direkt nach Absenden) eine leere Antwort-Blase in den
  Verlauf legen, die dann wächst — ersetzt die statische „…"-Tippblase.

## Error-Handling

- Backend: Fehler im Generator → `event: error\ndata: {"detail": "..."}`.
  Retrieval-503 (keine Collection) bleibt über die `get_collection`-Dependency
  erhalten — greift, bevor der Stream startet.
- Frontend: `try/catch` um den Reader, `finally` setzt `laedt` zurück (wie bisher).

## Tests (`backend/tests/test_chat.py`)

- Bestehende 3 Tests bleiben unangetastet.
- **1 neuer Test** für `/chat/stream`:
  - `AsyncAnthropic` + Collection via `app.dependency_overrides` mocken.
  - Stream konsumieren und prüfen: Reihenfolge `sources` → `token`(s) → `done`,
    Quellen korrekt, zusammengesetzter Token-Text = erwartete Antwort.

## Bewusst NICHT im Scope (YAGNI)

- CORS-Härtung, Konversations-Gedächtnis, Task-Queue → separate Härtungs-Option.
- Reconnect/Retry des Streams im Frontend.
- Abbruch-Button („stop generating").

## Verknüpfung

- Lernlücke: async-Zweitlösung (`httpx.AsyncClient`/`AsyncAnthropic` + `await`) aus
  Quiz 2026-06-23 — wird hier praktisch geschlossen.
