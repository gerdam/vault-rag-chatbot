# Konversations-Gedächtnis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der Vault-RAG-Chatbot wird mehrturnfähig — Verlauf pro `session_id` in SQLite, Retrieval mit Kontext, beide Endpoints (`/chat` + `/chat/stream`).

**Architecture:** Backend wird zustandsbehaftet pro Session. Pro Request: Verlauf aus SQLite laden → Retrieval-Query aus letzten User-Fragen bauen → Chunks holen → `messages`-Array (Verlauf + neue Frage) an Claude → User-Nachricht + Antwort nach Erfolg speichern. Gemeinsame Logik liegt isoliert in `backend/history.py`.

**Tech Stack:** FastAPI, SQLite (`sqlite3`), Anthropic SDK (sync + AsyncAnthropic), ChromaDB, pytest, React/Vite/TS.

**Referenz-Spec:** `docs/superpowers/specs/2026-06-25-konversations-gedaechtnis-design.md`

---

## File Structure

- **Create** `backend/history.py` — gesamte Gedächtnis-Logik: Schema, `get_history_db`-Dependency, `save_message`, `load_history`, `build_retrieval_query`, `build_messages`. Eine klare Verantwortung: Verlauf laden/speichern + Query-/Messages-Aufbau.
- **Create** `backend/tests/test_history.py` — Unit-Tests für `history.py` (Temp-DB + reine Helfer).
- **Modify** `backend/main.py` — `ChatRequest.session_id`; beide Endpoints nutzen `history`.
- **Modify** `backend/tests/test_chat.py` — bestehende Tests um `session_id` + DB-Override ergänzen; Memory- und Fehler-Tests hinzufügen.
- **Modify** `frontend/src/App.tsx` — `session_id` (UUID) mitschicken + „Neuer Chat"-Button.
- **Modify** `docker-compose.yml` — Volume + `HISTORY_DB_PATH` für die History-DB.

---

## Task 1: history.py — DB-Schicht (Schema, Verbindung, save/load)

**Files:**
- Create: `backend/history.py`
- Test: `backend/tests/test_history.py`

- [x] **Step 1: Failing test schreiben** — `backend/tests/test_history.py`

```python
"""
Unit-Tests für history.py. Temp-DB via tmp_path (echtes SQLite, aber wegwerfbar);
die reinen Helfer (build_*) brauchen gar keine DB.
"""

import sqlite3

import history


def _conn(tmp_path):
    conn = sqlite3.connect(tmp_path / "h.db")
    history.init_db(conn)
    return conn


def test_save_and_load_roundtrip(tmp_path):
    conn = _conn(tmp_path)
    history.save_message(conn, "s1", "user", "Frage 1")
    history.save_message(conn, "s1", "assistant", "Antwort 1")
    h = history.load_history(conn, "s1", 10)
    assert h == [
        {"role": "user", "content": "Frage 1"},
        {"role": "assistant", "content": "Antwort 1"},
    ]


def test_load_history_isoliert_sessions(tmp_path):
    conn = _conn(tmp_path)
    history.save_message(conn, "s1", "user", "A")
    history.save_message(conn, "s2", "user", "B")
    assert history.load_history(conn, "s1", 10) == [{"role": "user", "content": "A"}]


def test_load_history_kappt_auf_limit_chronologisch(tmp_path):
    conn = _conn(tmp_path)
    for i in range(5):
        history.save_message(conn, "s1", "user", f"m{i}")
    h = history.load_history(conn, "s1", 2)
    # die LETZTEN 2, aber chronologisch (alt -> neu)
    assert [m["content"] for m in h] == ["m3", "m4"]
```

- [x] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `cd backend && .venv\Scripts\python.exe -m pytest tests/test_history.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'history'` (bzw. `AttributeError`).

- [x] **Step 3: history.py mit DB-Schicht implementieren** — `backend/history.py`

```python
"""
history.py — Persistentes Konversations-Gedächtnis pro Session (SQLite).

Jeder Request bekommt über get_history_db eine frische Verbindung
(yield-Dependency: finally schließt sie IMMER, auch bei Exception).
Die reinen Helfer (build_retrieval_query, build_messages) sind DB-frei
und damit isoliert testbar.
"""

import os
import sqlite3

HISTORY_DB_PATH = os.getenv("HISTORY_DB_PATH", "./history.db")
HISTORY_MAX_TURNS = int(os.getenv("HISTORY_MAX_TURNS", "10"))
RETRIEVAL_CONTEXT_TURNS = int(os.getenv("RETRIEVAL_CONTEXT_TURNS", "3"))


def init_db(conn: sqlite3.Connection) -> None:
    """Legt Schema + Index idempotent an (mehrfacher Aufruf schadet nicht)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role       TEXT NOT NULL,
            content    TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_session ON messages(session_id)")
    conn.commit()


def get_history_db():
    """yield-Dependency: frische Verbindung pro Request, im finally geschlossen.

    check_same_thread=False, weil der Streaming-Endpoint die Verbindung im
    Event-Loop-Thread nutzt, während die Dependency im Threadpool laufen kann.
    Jeder Request hat seine eigene Verbindung — sie wird NICHT über Requests
    geteilt, daher ist das unbedenklich.
    """
    conn = sqlite3.connect(HISTORY_DB_PATH, check_same_thread=False)
    try:
        init_db(conn)
        yield conn          # reingeben …
    finally:
        conn.close()        # … UND danach immer aufräumen


def save_message(conn: sqlite3.Connection, session_id: str, role: str, content: str) -> None:
    conn.execute(
        "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
        (session_id, role, content),
    )
    conn.commit()


def load_history(conn: sqlite3.Connection, session_id: str, limit: int) -> list[dict]:
    """Letzte `limit` Nachrichten der Session, chronologisch (alt -> neu)."""
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id = ? "
        "ORDER BY id DESC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    return [{"role": r, "content": c} for (r, c) in reversed(rows)]
```

- [x] **Step 4: Tests laufen lassen, grün prüfen**

Run: `cd backend && .venv\Scripts\python.exe -m pytest tests/test_history.py -v`
Expected: PASS — 3 Tests grün.

- [x] **Step 5: Commit**

```bash
git add backend/history.py backend/tests/test_history.py
git commit -m "[backend] history: SQLite-Schicht fuer Session-Verlauf (save/load)"
```

---

## Task 2: history.py — reine Helfer (Retrieval-Query + Messages-Array)

**Files:**
- Modify: `backend/history.py`
- Test: `backend/tests/test_history.py`

- [x] **Step 1: Failing tests anhängen** — ans Ende von `backend/tests/test_history.py`

```python
def test_build_retrieval_query_verkettet_letzte_user_fragen():
    h = [
        {"role": "user", "content": "Was ist Ownership?"},
        {"role": "assistant", "content": "Ownership ist ..."},
    ]
    q = history.build_retrieval_query(h, "Und Borrowing?", n=3)
    assert q == "Was ist Ownership? Und Borrowing?"


def test_build_retrieval_query_ohne_history_nur_neue_frage():
    assert history.build_retrieval_query([], "Erste Frage", n=3) == "Erste Frage"


def test_build_retrieval_query_kappt_auf_n_user_fragen():
    h = [
        {"role": "user", "content": "f1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "f2"},
        {"role": "assistant", "content": "a2"},
    ]
    # n=1 -> nur die letzte User-Frage + die neue
    assert history.build_retrieval_query(h, "neu", n=1) == "f2 neu"


def test_build_messages_haengt_neue_user_nachricht_an():
    h = [{"role": "user", "content": "A"}, {"role": "assistant", "content": "B"}]
    msgs = history.build_messages(h, "C")
    assert msgs == [
        {"role": "user", "content": "A"},
        {"role": "assistant", "content": "B"},
        {"role": "user", "content": "C"},
    ]
```

- [x] **Step 2: Test laufen lassen, Fehlschlag prüfen**

Run: `cd backend && .venv\Scripts\python.exe -m pytest tests/test_history.py -k build -v`
Expected: FAIL — `AttributeError: module 'history' has no attribute 'build_retrieval_query'`.

- [x] **Step 3: Helfer in history.py ergänzen** — ans Ende von `backend/history.py`

```python
def build_retrieval_query(history: list[dict], new_message: str,
                          n: int = RETRIEVAL_CONTEXT_TURNS) -> str:
    """Verkettet die letzten n User-Fragen aus history + new_message.

    Damit behält das Retrieval bei kurzen Folgefragen ('und warum?') Kontext —
    der Embedding-Vektor trifft sonst die falschen Chunks.
    """
    user_msgs = [m["content"] for m in history if m["role"] == "user"]
    letzte = user_msgs[-n:]
    return " ".join([*letzte, new_message])


def build_messages(history: list[dict], new_message: str) -> list[dict]:
    """history (user/assistant) + neue User-Nachricht fürs Anthropic-Array."""
    return [*history, {"role": "user", "content": new_message}]
```

- [x] **Step 4: Tests laufen lassen, grün prüfen**

Run: `cd backend && .venv\Scripts\python.exe -m pytest tests/test_history.py -v`
Expected: PASS — 7 Tests grün.

- [x] **Step 5: Commit**

```bash
git add backend/history.py backend/tests/test_history.py
git commit -m "[backend] history: build_retrieval_query + build_messages"
```

---

## Task 3: /chat verdrahten (session_id + Gedächtnis)

**Files:**
- Modify: `backend/main.py` (Imports, `ChatRequest`, `chat`-Endpoint)
- Modify: `backend/tests/test_chat.py` (bestehende /chat-Tests + Memory-Test)

- [x] **Step 1: Bestehende /chat-Tests anpassen + DB-Fixture + Memory-Test** — `backend/tests/test_chat.py`

Oben bei den Imports `sqlite3` und `history` ergänzen:

```python
import sqlite3

import pytest
from fastapi.testclient import TestClient

import history
import main
```

Nach der bestehenden `client`-Fixture diese Fixture einfügen:

```python
@pytest.fixture
def history_db(tmp_path):
    """Überschreibt get_history_db mit einer wegwerfbaren Temp-DB.

    Ohne diesen Override würden die Endpoints die echte ./history.db öffnen.
    Gibt den DB-Pfad zurück, damit Tests die gespeicherten Zeilen prüfen können.
    """
    db_path = tmp_path / "h.db"

    def _override():
        conn = sqlite3.connect(db_path, check_same_thread=False)
        history.init_db(conn)
        try:
            yield conn
        finally:
            conn.close()

    main.app.dependency_overrides[history.get_history_db] = _override
    return db_path
```

Die drei bestehenden /chat-Tests bekommen `history_db` als Parameter und `session_id` im Body. Ersetze sie vollständig durch:

```python
def test_chat_liefert_antwort_und_deduplizierte_quellen(client, history_db):
    docs = ["Rust ist speichersicher."]
    metas = [{"datei": "rust.md"}, {"datei": "rust.md"}]  # Duplikat -> dedupliziert
    main.app.dependency_overrides[main.get_collection] = lambda: FakeCollection(docs, metas)
    main.app.dependency_overrides[main.get_claude] = lambda: FakeClaude()

    r = client.post("/chat", json={"message": "Ist Rust sicher?", "session_id": "s1"})

    assert r.status_code == 200
    body = r.json()
    assert body["antwort"] == "Laut den Notizen ist Rust speichersicher."
    assert body["quellen"] == ["rust.md"]


def test_chat_ohne_treffer_meldet_keine_notizen(client, history_db):
    main.app.dependency_overrides[main.get_collection] = lambda: FakeCollection([], [])
    main.app.dependency_overrides[main.get_claude] = lambda: FakeClaude()

    r = client.post("/chat", json={"message": "irgendwas", "session_id": "s1"})

    assert r.status_code == 200
    assert r.json()["antwort"] == "Keine relevanten Notizen gefunden."
    assert r.json()["quellen"] == []


def test_chat_ohne_index_gibt_503(client, history_db):
    def kein_index():
        raise main.HTTPException(status_code=503, detail="Vault nicht indexiert.")

    main.app.dependency_overrides[main.get_collection] = kein_index
    main.app.dependency_overrides[main.get_claude] = lambda: FakeClaude()

    r = client.post("/chat", json={"message": "egal", "session_id": "s1"})

    assert r.status_code == 503
```

Außerdem aufzeichnende Fakes + Memory-Test ans Ende der Datei anfügen:

```python
# --- Aufzeichnende Fakes für den Memory-Test -----------------------------
class RecordingCollection:
    """Wie FakeCollection, merkt sich aber die empfangenen query_texts."""

    def __init__(self, docs, metas):
        self._docs = docs
        self._metas = metas
        self.queries: list[str] = []

    def query(self, query_texts, n_results):
        self.queries.append(query_texts[0])
        return {"documents": [self._docs], "metadatas": [self._metas]}


class RecordingClaude:
    """Merkt sich das messages-Array jedes create()-Aufrufs."""

    def __init__(self):
        self.calls: list[list] = []
        outer = self

        class _Msgs:
            def create(self, **kwargs):
                outer.calls.append(kwargs["messages"])

                class _R:
                    content = [_FakeText()]

                return _R()

        self.messages = _Msgs()


def test_chat_merkt_sich_verlauf_ueber_zwei_requests(client, history_db):
    col = RecordingCollection(["Rust ist sicher."], [{"datei": "rust.md"}])
    claude = RecordingClaude()
    main.app.dependency_overrides[main.get_collection] = lambda: col
    main.app.dependency_overrides[main.get_claude] = lambda: claude

    r1 = client.post("/chat", json={"message": "Was ist Ownership?", "session_id": "s1"})
    r2 = client.post("/chat", json={"message": "Und Borrowing?", "session_id": "s1"})
    assert r1.status_code == 200 and r2.status_code == 200

    # 2. Claude-Aufruf enthält den ersten Turn (user + assistant) VOR der neuen Frage
    zweite = claude.calls[1]
    assert zweite[0] == {"role": "user", "content": "Was ist Ownership?"}
    assert zweite[1]["role"] == "assistant"
    assert zweite[-1] == {"role": "user", "content": "Und Borrowing?"}

    # Retrieval-Query des 2. Requests trägt die erste Frage als Kontext mit
    assert "Was ist Ownership?" in col.queries[1]
    assert "Und Borrowing?" in col.queries[1]

    # DB enthält 4 Zeilen (2 user, 2 assistant)
    conn = sqlite3.connect(history_db)
    n = conn.execute("SELECT COUNT(*) FROM messages WHERE session_id='s1'").fetchone()[0]
    conn.close()
    assert n == 4
```

- [x] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd backend && .venv\Scripts\python.exe -m pytest tests/test_chat.py -k "chat and not stream" -v`
Expected: FAIL — `session_id`-Pflichtfeld fehlt noch in `ChatRequest` (422) bzw. Endpoint nutzt history noch nicht.

- [x] **Step 3: main.py — Imports, ChatRequest, /chat-Endpoint**

Imports ergänzen (nach `import os`):

```python
import sqlite3

import history
```

`ChatRequest` erweitern:

```python
class ChatRequest(BaseModel):
    message: str
    session_id: str
```

Den `chat`-Endpoint vollständig ersetzen durch:

```python
@app.post("/chat", response_model=ChatResponse)
def chat(
    req: ChatRequest,
    col=Depends(get_collection),
    claude: anthropic.Anthropic = Depends(get_claude),
    db: sqlite3.Connection = Depends(history.get_history_db),
):
    verlauf = history.load_history(db, req.session_id, history.HISTORY_MAX_TURNS * 2)
    retrieval_query = history.build_retrieval_query(verlauf, req.message)

    results = col.query(query_texts=[retrieval_query], n_results=N_RESULTS)
    chunks: list[str] = results["documents"][0]
    metas: list[dict] = results["metadatas"][0]

    if not chunks:
        antwort = "Keine relevanten Notizen gefunden."
        history.save_message(db, req.session_id, "user", req.message)
        history.save_message(db, req.session_id, "assistant", antwort)
        return ChatResponse(antwort=antwort, quellen=[])

    kontext = "\n\n---\n\n".join(chunks)
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
        quellen=list({m["datei"] for m in metas}),
    )
```

- [x] **Step 4: Tests laufen lassen, grün prüfen**

Run: `cd backend && .venv\Scripts\python.exe -m pytest tests/test_chat.py -k "chat and not stream" -v`
Expected: PASS — die drei /chat-Tests + Memory-Test grün.

- [x] **Step 5: Commit**

```bash
git add backend/main.py backend/tests/test_chat.py
git commit -m "[backend] chat: Session-Verlauf in /chat (session_id, Retrieval-Kontext)"
```

---

## Task 4: /chat/stream verdrahten (session_id + Gedächtnis)

**Files:**
- Modify: `backend/main.py` (`chat_stream`-Endpoint)
- Modify: `backend/tests/test_chat.py` (Streaming-Test erweitern + Fehler-Test)

- [x] **Step 1: Bestehenden Streaming-Test erweitern + Fehler-Test** — `backend/tests/test_chat.py`

Den bestehenden `test_chat_stream_sendet_sources_dann_tokens_dann_done` vollständig ersetzen durch (Body um `session_id`, Fixture `history_db`, Persistenz-Check):

```python
def test_chat_stream_sendet_sources_dann_tokens_dann_done(client, history_db):
    docs = ["Rust ist speichersicher."]
    metas = [{"datei": "rust.md"}, {"datei": "rust.md"}]
    texte = ["Laut den Notizen ", "ist Rust speichersicher."]

    main.app.dependency_overrides[main.get_collection] = lambda: FakeCollection(docs, metas)
    main.app.dependency_overrides[main.get_async_claude] = lambda: FakeAsyncClaude(texte)

    r = client.post("/chat/stream", json={"message": "Ist Rust sicher?", "session_id": "s7"})

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(r.text)
    assert events[0] == ("sources", {"quellen": ["rust.md"]})
    token_texte = [d["text"] for (e, d) in events if e == "token"]
    assert "".join(token_texte) == "Laut den Notizen ist Rust speichersicher."
    assert events[-1] == ("done", {})

    # nach 'done' ist der Turn persistiert: user-Frage + vollständige Antwort
    conn = sqlite3.connect(history_db)
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id='s7' ORDER BY id"
    ).fetchall()
    conn.close()
    assert rows == [
        ("user", "Ist Rust sicher?"),
        ("assistant", "Laut den Notizen ist Rust speichersicher."),
    ]
```

Fehler-Test + dazu nötige Fakes ans Ende der Datei anfügen:

```python
# --- Async-Fake, der mitten im Stream wirft -----------------------------
class _FailingAsyncStream:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def text_stream(self):
        async def gen():
            raise RuntimeError("Boom")
            yield ""  # pragma: no cover — macht gen() zur async-Generator-Funktion
        return gen()


class _FailingAsyncMessages:
    def stream(self, **kwargs):
        return _FailingAsyncStream()


class FailingAsyncClaude:
    messages = _FailingAsyncMessages()


def test_chat_stream_speichert_nichts_bei_fehler(client, history_db):
    main.app.dependency_overrides[main.get_collection] = lambda: FakeCollection(
        ["X"], [{"datei": "a.md"}]
    )
    main.app.dependency_overrides[main.get_async_claude] = lambda: FailingAsyncClaude()

    r = client.post("/chat/stream", json={"message": "Frage", "session_id": "s9"})

    events = _parse_sse(r.text)
    assert any(e == "error" for (e, _) in events)

    # bei Fehler darf KEIN Turn gespeichert sein
    conn = sqlite3.connect(history_db)
    rows = conn.execute("SELECT * FROM messages WHERE session_id='s9'").fetchall()
    conn.close()
    assert rows == []
```

- [x] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd backend && .venv\Scripts\python.exe -m pytest tests/test_chat.py -k stream -v`
Expected: FAIL — `/chat/stream` verlangt `session_id` noch nicht / speichert noch nicht.

- [x] **Step 3: main.py — chat_stream-Endpoint ersetzen**

```python
@app.post("/chat/stream")
async def chat_stream(
    req: ChatRequest,
    col=Depends(get_collection),
    claude: anthropic.AsyncAnthropic = Depends(get_async_claude),
    db: sqlite3.Connection = Depends(history.get_history_db),
):
    # Verlauf + Retrieval-Query vor dem Streamen bestimmen (kleine sync-Reads).
    # Die db-Verbindung bleibt offen, bis die StreamingResponse fertig ist —
    # daher kann der Generator weiter unten gefahrlos speichern.
    verlauf = history.load_history(db, req.session_id, history.HISTORY_MAX_TURNS * 2)
    retrieval_query = history.build_retrieval_query(verlauf, req.message)

    async def event_stream():
        # 1. Retrieval — sync-Lib bewusst in den Threadpool ausgelagert
        results = await run_in_threadpool(
            col.query, query_texts=[retrieval_query], n_results=N_RESULTS
        )
        chunks: list[str] = results["documents"][0]
        metas: list[dict] = results["metadatas"][0]

        if not chunks:
            antwort = "Keine relevanten Notizen gefunden."
            yield _sse("sources", {"quellen": []})
            yield _sse("token", {"text": antwort})
            history.save_message(db, req.session_id, "user", req.message)
            history.save_message(db, req.session_id, "assistant", antwort)
            yield _sse("done", {})
            return

        quellen = list({m["datei"] for m in metas})
        yield _sse("sources", {"quellen": quellen})

        kontext = "\n\n---\n\n".join(chunks)

        # 2. Tokens streamen und parallel zur vollständigen Antwort sammeln
        teile: list[str] = []
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
                messages=history.build_messages(verlauf, req.message),
            ) as stream:
                async for text in stream.text_stream:
                    teile.append(text)
                    yield _sse("token", {"text": text})
        except Exception as e:  # noqa: BLE001 — Fehler an den Client durchreichen
            yield _sse("error", {"detail": str(e)})
            return  # bei Fehler NICHT speichern

        # 3. Erst nach erfolgreichem 'done' den Turn persistieren
        history.save_message(db, req.session_id, "user", req.message)
        history.save_message(db, req.session_id, "assistant", "".join(teile))
        yield _sse("done", {})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

- [x] **Step 4: Gesamte Backend-Suite laufen lassen, grün prüfen**

Run: `cd backend && .venv\Scripts\python.exe -m pytest -v`
Expected: PASS — alle Tests aus `test_history.py` + `test_chat.py` grün.

- [x] **Step 5: Commit**

```bash
git add backend/main.py backend/tests/test_chat.py
git commit -m "[backend] chat-stream: Session-Verlauf + Speichern nach done"
```

---

## Task 5: Frontend — session_id (UUID) + „Neuer Chat"

**Files:**
- Modify: `frontend/src/App.tsx`

- [x] **Step 1: session_id-Ref anlegen** — in `App()`, nach den `useState`-Zeilen (nach `const [fehler, ...]`):

```tsx
  // Eine Session-ID pro Chat. Frontend generiert sie; Backend nutzt sie als
  // Schlüssel für den gespeicherten Verlauf. crypto.randomUUID() ist im Browser nativ.
  const sessionId = useRef(crypto.randomUUID());
```

- [x] **Step 2: session_id im Request mitschicken** — im `fetch`-Body von `frageSenden`:

```tsx
        body: JSON.stringify({ message: frage, session_id: sessionId.current }),
```

- [x] **Step 3: „Neuer Chat" — Handler ergänzen** — in `App()`, vor dem `return`:

```tsx
  // Startet eine frische Session: neue ID + leerer Verlauf.
  function neuerChat() {
    sessionId.current = crypto.randomUUID();
    setVerlauf([]);
    setFehler(null);
    setEingabe("");
  }
```

- [x] **Step 4: „Neuer Chat"-Button im Header rendern** — im `<header>` nach dem `<p className="untertitel">…</p>`:

```tsx
        <button className="neuer-chat" onClick={neuerChat} disabled={laedt}>
          Neuer Chat
        </button>
```

- [x] **Step 5: Build prüfen**

Run: `cd frontend && npm run build`
Expected: Build erfolgreich, keine TypeScript-Fehler (`crypto.randomUUID` ist typisiert).

- [x] **Step 6: Manuell verifizieren (Browser)**

Backend starten: `cd backend && .venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000`
Frontend: `cd frontend && npm run dev` → http://localhost:5173

Prüfen:
1. Frage stellen → Antwort streamt wie bisher.
2. Folgefrage ohne Namen (z. B. „und warum?") → Antwort bezieht sich auf das vorige Thema.
3. „Neuer Chat" → Verlauf leer, neue Session (Folgefrage hat keinen Kontext mehr).

- [x] **Step 7: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "[frontend] chat: session_id (UUID) mitschicken + Neuer-Chat-Button"
```

---

## Task 6: Docker — History-DB persistent machen

**Files:**
- Modify: `docker-compose.yml`

- [x] **Step 1: Volume + HISTORY_DB_PATH ergänzen** — im `backend`-Service:

`environment` um eine Zeile erweitern:

```yaml
    environment:
      - CHROMA_PATH=/data/chroma   # ueberschreibt .env; load_dotenv override-t echte Env-Vars NICHT
      - N_RESULTS=5
      - HISTORY_DB_PATH=/data/history.db   # Verlauf-DB im gemounteten /data-Verzeichnis
```

`volumes` um eine Zeile erweitern:

```yaml
    volumes:
      - ./backend/chroma_data:/data/chroma   # bestehender Index (36.227 Chunks) als Volume
      - ./backend/data:/data                 # persistente History-DB (history.db landet hier)
```

> Hinweis: `/data` umfasst dann sowohl `chroma` als auch `history.db`. Falls der bestehende `chroma_data`-Mount Vorrang behalten soll, ist die Reihenfolge unkritisch — Docker mountet beide Pfade; `history.db` liegt unter `/data/history.db`, der Index unter `/data/chroma`.

- [x] **Step 2: .gitignore prüfen/ergänzen** — sicherstellen, dass die DB nicht eingecheckt wird:

Run: `cd backend && git check-ignore history.db data/history.db`
Falls nichts ausgegeben wird (= nicht ignoriert), in `backend/.gitignore` ergänzen:

```
history.db
data/
```

- [x] **Step 3: Compose-Build/Up testen**

Run: `docker compose up -d --build`
Dann: `curl http://localhost:8000/health` → `{"status":"ok","chunks":36227}` (oder aktueller Wert).
Im Browser http://localhost:5173 zwei Folgefragen stellen, dann `docker compose restart backend`, prüfen dass der Verlauf nach Neustart noch wirkt (gleiche Session-ID → Button NICHT drücken).
Aufräumen optional: `docker compose down`.

- [x] **Step 4: Commit**

```bash
git add docker-compose.yml backend/.gitignore
git commit -m "[devops] compose: History-DB als persistentes Volume (HISTORY_DB_PATH)"
```

---

## Task 7: Abschluss-Verifikation

**Files:** keine (nur Verifikation)

- [x] **Step 1: Vollständige Backend-Suite**

Run: `cd backend && .venv\Scripts\python.exe -m pytest -v`
Expected: PASS — alle Tests grün (test_history.py: 7, test_chat.py: 6).

- [x] **Step 2: Frontend-Build**

Run: `cd frontend && npm run build`
Expected: erfolgreich, keine TS-Fehler.

- [x] **Step 3: Spec-Abgleich** — kurz gegen `docs/superpowers/specs/2026-06-25-konversations-gedaechtnis-design.md` prüfen, dass alle „Getroffene Entscheidungen" umgesetzt sind. Keine Code-Änderung, nur Häkchen.

---

## Self-Review (vom Plan-Autor durchgeführt)

**Spec-Coverage:** SQLite-Speicher → Task 1; Retrieval-Verkettung → Task 2/3; beide Endpoints → Task 3+4; yield-Dependency → Task 1; session_id-Pflichtfeld → Task 3; Streaming-Speichern nach done + kein Speichern bei Fehler → Task 4; Frontend-UUID + Neuer-Chat → Task 5; Docker-Volume + Caps (HISTORY_MAX_TURNS/RETRIEVAL_CONTEXT_TURNS als Env) → Task 1/6. Alle Spec-Punkte abgedeckt.

**Type-Konsistenz:** `get_history_db`, `init_db`, `save_message`, `load_history`, `build_retrieval_query`, `build_messages` einheitlich über alle Tasks benannt; `ChatRequest.session_id` durchgängig; `history.HISTORY_MAX_TURNS * 2` als Message-Limit in beiden Endpoints identisch.

**YAGNI:** kein Session-Cleanup, keine Auth, keine Verlaufs-Zusammenfassung (bewusst ausgelassen, siehe Spec).
