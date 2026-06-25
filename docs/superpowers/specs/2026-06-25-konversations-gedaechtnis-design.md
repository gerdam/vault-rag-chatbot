# Design: Konversations-Gedächtnis für den Vault-RAG-Chatbot

> Datum: 2026-06-25
> Status: genehmigt (Brainstorming abgeschlossen)
> Projekt: vault-rag-chatbot (gerdam/vault-rag-chatbot)

## Ziel

Der Chatbot soll mehrturnfähig werden: Folgefragen ("und warum?", "wie hängt das
mit X zusammen?") berücksichtigen den bisherigen Gesprächsverlauf — sowohl bei der
Antwortgenerierung (Claude bekommt den Verlauf) als auch beim Retrieval (ChromaDB
sucht mit Kontext statt nur der kurzen Folgefrage).

## Getroffene Entscheidungen

| Frage | Entscheidung |
|-------|--------------|
| Wo lebt der Verlauf? | Backend speichert pro `session_id` (zustandsbehaftet) |
| Speichermedium | SQLite (persistent) |
| Retrieval-Basis bei Folgefragen | letzte N User-Nachrichten verkettet (N=3) |
| Endpoint-Scope | beide: `/chat` **und** `/chat/stream` |
| Session-ID | Frontend generiert UUID (`crypto.randomUUID()`) |
| "Neuer Chat"-Button | ja, minimal (neue UUID + Anzeige leeren) |

## Architektur

Ablauf jeder Anfrage (identisch für beide Endpoints):

```
Request {message, session_id}
  1. Verlauf aus SQLite laden        (gekappt auf letzte HISTORY_MAX_TURNS)
  2. Retrieval-Query bauen           (letzte 3 User-Fragen + neue Frage, verkettet)
  3. ChromaDB: relevante Chunks holen
  4. messages-Array bauen            (Verlauf + neue User-Nachricht)
  5. Claude aufrufen                 (sync: /chat · async-stream: /chat/stream)
  6. neue User-Nachricht + Antwort   in SQLite speichern
```

## Datenmodell (SQLite)

Neue Datei `backend/history.py`. Tabelle:

```sql
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,        -- 'user' | 'assistant'
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_session ON messages(session_id);
```

Schema wird beim Start idempotent angelegt (`CREATE TABLE IF NOT EXISTS`).
DB-Pfad via Env `HISTORY_DB_PATH` (Default `./history.db`). In Docker als Volume
gemountet (analog `chroma_data`), sonst geht der Verlauf bei Container-Neustart verloren.

## SQLite-Verbindung als `yield`-Dependency

```python
def get_history_db():
    conn = sqlite3.connect(HISTORY_DB_PATH)
    try:
        yield conn          # reingeben …
    finally:
        conn.close()        # … UND danach IMMER aufräumen (auch bei Exception)
```

Bewusst eine `yield`-Dependency (nicht `return`): Die Verbindung muss nach jedem
Request garantiert geschlossen werden — der `finally`-Block läuft immer.
sqlite3-Connection wird pro Request frisch geöffnet (kein `@lru_cache`-Singleton),
weil FastAPI Endpoints im Threadpool ausführt und sqlite3-Connections nicht
thread-sicher geteilt werden sollen.

## Gemeinsame Helfer (gegen Doppelcode in beiden Endpoints)

In `backend/history.py`:

- `init_db(conn)` — Schema idempotent anlegen
- `load_history(conn, session_id, max_turns) -> list[dict]`
  — letzte Turns als `[{"role", "content"}, …]`, chronologisch (alt → neu)
- `save_message(conn, session_id, role, content)` — eine Zeile einfügen
- `build_retrieval_query(history, new_message, n=3) -> str`
  — verkettet die letzten n User-Fragen aus history + new_message
- `build_messages(history, new_message) -> list[dict]`
  — `history` + `{"role": "user", "content": new_message}` fürs Anthropic-Array

Beide Endpoints rufen nur diese Helfer — Retrieval-/History-Logik existiert **einmal**.

## API-Änderung

```python
class ChatRequest(BaseModel):
    message: str
    session_id: str        # neu, Pflicht → fehlt er: 422 (Pydantic)
```

- `/chat`: Response unverändert (`antwort` + `quellen`).
- `/chat/stream`: SSE-Events unverändert (`sources`/`token`/`done`/`error`).
  Tokens werden während des Streams mitgesammelt; die **vollständige** Antwort wird
  erst **nach** dem `done`-Event gespeichert. Bei `error` wird **nichts** gespeichert
  (kein halber Assistant-Turn im Verlauf).

**Speicher-Regel (beide Endpoints):** User-Nachricht und Claude-Antwort werden
gemeinsam erst **nach erfolgreicher** Antwort gespeichert. So verschmutzt ein
fehlgeschlagener Turn den Verlauf nicht (weder eine User-Nachricht ohne Antwort noch
eine halbe Antwort).

## Konfiguration (Env-Variablen)

| Variable | Default | Zweck |
|----------|---------|-------|
| `HISTORY_DB_PATH` | `./history.db` | SQLite-Datei |
| `HISTORY_MAX_TURNS` | `10` | nur die letzten 10 Turns gehen an Claude (Token-Budget); gespeichert wird alles |
| `RETRIEVAL_CONTEXT_TURNS` | `3` | wie viele letzte User-Fragen ins Retrieval-Query |

## Frontend (`frontend/src/App.tsx`)

- Beim Laden einmal `sessionId = crypto.randomUUID()` in `useRef` festhalten.
- Bei jedem `/chat/stream`-POST `session_id` im Body mitschicken.
- "Neuer Chat"-Button: neue UUID erzeugen + Nachrichten-Anzeige leeren.

## Fehlerbehandlung

- Fehlender `session_id` → 422 (Pydantic-Pflichtfeld).
- ChromaDB nicht indexiert → bestehende 503 via `get_collection` (unverändert).
- Claude-Fehler beim Streaming → `error`-Event (bestehend); Turn wird **nicht** gespeichert.
- DB-Verbindung wird per `finally` immer geschlossen.

## Tests (`backend/tests/`)

- Temp-DB via `tmp_path`; `get_history_db` per `app.dependency_overrides` darauf zeigen.
  Chroma + Claude weiterhin als Fakes (bestehendes Muster).
- **Kernfall (Gedächtnis):** zwei Requests mit *derselben* `session_id` →
  - zweiter Claude-Aufruf erhält den ersten Turn im `messages`-Array,
  - Retrieval-Query des zweiten Requests enthält die erste User-Frage,
  - DB enthält danach 4 Zeilen (2 user, 2 assistant).
- **Isolation:** zwei verschiedene `session_id` teilen keinen Verlauf.
- **Negativfall (Streaming-Fehler):** `error`-Event → kein Assistant-Eintrag in der DB.
- Bestehende Tests bleiben grün (ggf. `session_id` in vorhandene Requests ergänzen).

## Docker

`docker-compose.yml`: Die History-DB in ein gemountetes **Verzeichnis** legen, damit
der Verlauf Container-Neustarts übersteht — z. B. Mount `./backend/data:/data` und
`HISTORY_DB_PATH=/data/history.db`. Kein Single-File-Mount (Docker legt sonst ein
Verzeichnis an, wenn die Datei beim Start noch nicht existiert).

## Bewusst NICHT enthalten (YAGNI)

- Kein Session-Ablauf/Cleanup-Job (alte Sessions bleiben in der DB).
- Keine Authentifizierung / kein Session-Besitz (session_id = ungeschützter Schlüssel).
- Keine Zusammenfassung langer Verläufe (nur hartes Kappen auf N Turns).
- Kein Backend-seitiges Minten der session_id.
```

