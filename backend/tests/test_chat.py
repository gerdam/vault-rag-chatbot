"""
Tests für die /chat- und /health-Endpoints.

Kernidee: Dank Dependency Injection (Depends) lassen sich der ChromaDB- und
der Anthropic-Client über app.dependency_overrides durch Fakes ersetzen.
Die Tests laufen damit OHNE echte Vektordatenbank und OHNE Anthropic-API —
offline, schnell und kostenlos.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

import history
import main


# --- Fakes statt echter Clients -----------------------------------------
class FakeCollection:
    def __init__(self, docs, metas):
        self._docs = docs
        self._metas = metas

    def query(self, query_texts, n_results):
        return {"documents": [self._docs], "metadatas": [self._metas]}


class _FakeText:
    text = "Laut den Notizen ist Rust speichersicher."


class _FakeMessages:
    def create(self, **kwargs):
        class _R:
            content = [_FakeText()]

        return _R()


class FakeClaude:
    messages = _FakeMessages()


@pytest.fixture
def client():
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()  # nach jedem Test sauber zurücksetzen


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


def test_chat_liefert_antwort_und_deduplizierte_quellen(client, history_db):
    docs = ["Rust ist speichersicher."]
    # zwei Treffer aus derselben Datei -> Quellen müssen dedupliziert werden
    metas = [{"datei": "rust.md"}, {"datei": "rust.md"}]
    main.app.dependency_overrides[main.get_collection] = lambda: FakeCollection(docs, metas)
    main.app.dependency_overrides[main.get_claude] = lambda: FakeClaude()

    r = client.post("/chat", json={"message": "Ist Rust sicher?", "session_id": "s1"})

    assert r.status_code == 200
    body = r.json()
    assert body["antwort"] == "Laut den Notizen ist Rust speichersicher."
    assert body["quellen"] == ["rust.md"]  # dedupliziert


def test_chat_ohne_treffer_meldet_keine_notizen(client, history_db):
    main.app.dependency_overrides[main.get_collection] = lambda: FakeCollection([], [])
    main.app.dependency_overrides[main.get_claude] = lambda: FakeClaude()

    r = client.post("/chat", json={"message": "irgendwas", "session_id": "s1"})

    assert r.status_code == 200
    assert r.json()["antwort"] == "Keine relevanten Notizen gefunden."
    assert r.json()["quellen"] == []


def test_chat_ohne_index_gibt_503(client, history_db):
    # get_collection wirft die echte 503-HTTPException -> Endpoint läuft nicht an
    def kein_index():
        raise main.HTTPException(status_code=503, detail="Vault nicht indexiert.")

    main.app.dependency_overrides[main.get_collection] = kein_index
    main.app.dependency_overrides[main.get_claude] = lambda: FakeClaude()

    r = client.post("/chat", json={"message": "egal", "session_id": "s1"})

    assert r.status_code == 503


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


def test_chat_stream_sendet_sources_dann_tokens_dann_done(client, history_db):
    docs = ["Rust ist speichersicher."]
    metas = [{"datei": "rust.md"}, {"datei": "rust.md"}]  # Duplikat -> dedupliziert
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
