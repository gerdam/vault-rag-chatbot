# Vault-RAG-Chatbot

Web-Chatbot, der Fragen direkt auf Basis des eigenen Obsidian-Vaults beantwortet — Retrieval-Augmented Generation (RAG) mit ChromaDB + Claude API, ausgeliefert über FastAPI-Backend und React/Vite-Frontend, alles in Docker Compose.

> Produkt A von 3 im Rahmen der Lern-Roadmap "AI-first Fullstack-Informatiker bis Ende 2027" (Phase 3 — "3 fertige deploybare Produkte").

## Architektur

```
Browser (React + Vite)
      │  fetch POST /chat   →  http://localhost:8000
      ▼
FastAPI Backend (Container "backend")
      │
      ├─ ChromaDB ─ persistente Vektordatenbank (chroma_data)
      │    └─ Obsidian-Notizen als Chunks + Embeddings
      │
      └─ Claude API ─ generiert Antwort aus den Top-N Chunks als Kontext
```

Wichtig: Das Frontend läuft im Browser des Nutzers, also außerhalb des Docker-Netzwerks. Es spricht das Backend deshalb immer über den **host-exponierten Port** an (`localhost:8000`), nicht über den Compose-Service-Namen `backend`.

## Tech-Stack

| Schicht | Technologie |
|---|---|
| Frontend | React + Vite, TypeScript, ausgeliefert via nginx (Multi-Stage-Build) |
| Backend | FastAPI, Pydantic, async |
| Vektordatenbank | ChromaDB (lokal, persistent) |
| LLM | Claude API (Anthropic) |
| Infrastruktur | Docker Compose |

## Voraussetzungen

- Docker + Docker Compose
- Ein Anthropic-API-Key (`ANTHROPIC_API_KEY`)
- Lokaler Pfad zum Obsidian-Vault (für die einmalige Indexierung)

## Setup

1. Repository klonen, `.env` aus `.env.example` anlegen (niemals committen — in `.gitignore`):
   ```bash
   VAULT_PATH=/pfad/zu/deinem/obsidian-vault
   ANTHROPIC_API_KEY=sk-ant-...
   ```
2. Vault einmalig indexieren (`backend/index.py`) — liest alle `.md`-Dateien, chunkt sie und befüllt ChromaDB unter `chroma_data`.
3. App starten:
   ```bash
   docker compose up --build
   ```
4. Aufrufen:
   - Frontend: http://localhost:5173
   - Backend-Health-Check: http://localhost:8000

## API

**`POST /chat`**

```json
// Request
{ "message": "Was weiß ich über Docker Compose?" }

// Response
{ "antwort": "...", "quellen": ["10-Notes/Technik/..."] }
```

Statuscodes: `200` Erfolg · `422` ungültige Eingabe (Pydantic-Validierung schlägt vor Funktionsaufruf fehl) · `503` ChromaDB nicht indexiert.

## Projektstruktur

```
vault-rag-chatbot/
├── backend/
│   ├── main.py           # FastAPI App, /chat-Endpoint
│   ├── index.py          # Vault einlesen + ChromaDB befüllen
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/App.tsx        # Chat-UI (useState: Eingabe, Verlauf, Laden, Fehler)
│   └── Dockerfile         # Multi-Stage: Node-Build → nginx
├── docker-compose.yml
└── .env                    # nicht committen
```

## Bekannte Einschränkungen

Kein Nutzer-Login, keine Mehrbenutzer-Trennung, keine Streaming-Antworten. Vault-Änderungen erfordern manuelles erneutes Ausführen von `index.py` (kein automatischer Re-Index). `.env`-Secrets liegen unverschlüsselt — für ein echtes Production-Deployment fehlt noch ein Reverse-Proxy (nginx vor Frontend + Backend) und Secrets-Management.

## Status

MVP funktionsfähig (Indexierung, Chat-Endpoint, Frontend, Docker Compose getestet). Offen: GitHub-Push und Screenshot/Demo fürs Portfolio.
