"""
Tests für die /chat- und /health-Endpoints.

Kernidee: Dank Dependency Injection (Depends) lassen sich der ChromaDB- und
der Anthropic-Client über app.dependency_overrides durch Fakes ersetzen.
Die Tests laufen damit OHNE echte Vektordatenbank und OHNE Anthropic-API —
offline, schnell und kostenlos.
"""

import pytest
from fastapi.testclient import TestClient

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


def test_chat_liefert_antwort_und_deduplizierte_quellen(client):
    docs = ["Rust ist speichersicher."]
    # zwei Treffer aus derselben Datei -> Quellen müssen dedupliziert werden
    metas = [{"datei": "rust.md"}, {"datei": "rust.md"}]
    main.app.dependency_overrides[main.get_collection] = lambda: FakeCollection(docs, metas)
    main.app.dependency_overrides[main.get_claude] = lambda: FakeClaude()

    r = client.post("/chat", json={"message": "Ist Rust sicher?"})

    assert r.status_code == 200
    body = r.json()
    assert body["antwort"] == "Laut den Notizen ist Rust speichersicher."
    assert body["quellen"] == ["rust.md"]  # dedupliziert


def test_chat_ohne_treffer_meldet_keine_notizen(client):
    main.app.dependency_overrides[main.get_collection] = lambda: FakeCollection([], [])
    main.app.dependency_overrides[main.get_claude] = lambda: FakeClaude()

    r = client.post("/chat", json={"message": "irgendwas"})

    assert r.status_code == 200
    assert r.json()["antwort"] == "Keine relevanten Notizen gefunden."
    assert r.json()["quellen"] == []


def test_chat_ohne_index_gibt_503(client):
    # get_collection wirft die echte 503-HTTPException -> Endpoint läuft nicht an
    def kein_index():
        raise main.HTTPException(status_code=503, detail="Vault nicht indexiert.")

    main.app.dependency_overrides[main.get_collection] = kein_index
    main.app.dependency_overrides[main.get_claude] = lambda: FakeClaude()

    r = client.post("/chat", json={"message": "egal"})

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
