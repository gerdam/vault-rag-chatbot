# Vault-RAG-Chatbot

Ein RAG-Chatbot (Retrieval-Augmented Generation), der Fragen **ausschließlich
auf Basis eines persönlichen Obsidian-Vaults** beantwortet. Frage stellen →
relevante Notiz-Ausschnitte werden aus einer Vektordatenbank geholt → Claude
formuliert daraus eine Antwort **mit Quellenangabe**.

> Produkt A von 3 im Rahmen der Lern-Roadmap "AI-first Fullstack-Informatiker bis Ende 2027" (Phase 3 — "3 fertige deploybare Produkte").

## Architektur

```
Browser (React/Vite)
   │  POST /chat  { "message": "..." }
   ▼
FastAPI-Backend (Python)
   │  1. semantische Suche
   ▼
ChromaDB  ──►  5 relevanteste Chunks
   │  2. Chunks als Kontext
   ▼
Claude (claude-sonnet-4-6)
   │  3. Antwort auf Deutsch + Quellen
   ▼
zurück an den Browser
```

| Komponente | Technik | Ordner |
|------------|---------|--------|
| Frontend | React + TypeScript + Vite, ausgeliefert via nginx | `frontend/` |
| Backend | FastAPI + uvicorn | `backend/` |
| Vektor-DB | ChromaDB (persistent, lokal) | `backend/chroma_data/` |
| LLM | Anthropic Claude | — |

Der Browser läuft außerhalb des Docker-Netzwerks und kennt den Compose-Service-Namen `backend` nicht. Deshalb wird die Backend-URL (`http://localhost:8000`) dem Frontend-Image schon beim Build als `VITE_API_URL` mitgegeben (siehe `docker-compose.yml`) — zur Laufzeit im Browser muss es immer der host-exponierte Port sein, nie der Service-Name.

## Voraussetzungen

- Docker Desktop (für den einfachen Start)
- Ein Anthropic-API-Key
- Ein indexierter Vault (siehe „Index aufbauen")

## Einrichtung

1. **API-Key eintragen:** `backend/.env.example` nach `backend/.env` kopieren
   und den echten Key einsetzen:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   VAULT_PATH=C:\Pfad\zu\deinem\vault
   CHROMA_PATH=./chroma_data
   N_RESULTS=5
   ```
   > `.env` steht in `.gitignore` und wird nie committet.

2. **Index aufbauen** (einmalig / nach Vault-Änderungen):
   ```bash
   cd backend
   .venv\Scripts\python.exe index.py
   ```
   Liest alle `.md`-Dateien des Vaults, zerlegt sie in Chunks (500 Zeichen,
   50 Überlappung) und speichert sie in ChromaDB.

## Starten

### Mit Docker (empfohlen)

```bash
docker compose up -d        # bauen + starten
# → http://localhost:5173
docker compose down         # stoppen
```

### Manuell (zwei Terminals)

```bash
# Terminal 1 – Backend
cd backend
.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000

# Terminal 2 – Frontend
cd frontend
npm install        # einmalig
npm run dev        # → http://localhost:5173
```

## API

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| `GET` | `/health` | Status + Anzahl indexierter Chunks |
| `POST` | `/chat` | Body `{ "message": "..." }` → `{ "antwort": "...", "quellen": [...] }` |

Statuscodes: `200` Erfolg · `422` ungültige Eingabe (Pydantic-Validierung lehnt den Request vor Funktionsaufruf ab) · `503` Vault noch nicht indexiert.

## Projektstruktur

```
vault-rag-chatbot/
├── backend/
│   ├── main.py            # FastAPI App: /health, /chat
│   ├── index.py           # Vault einlesen, chunken, in ChromaDB schreiben
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── .env                # nicht committen
│   └── chroma_data/         # ChromaDB-Index (nicht committen)
├── frontend/
│   ├── src/
│   ├── Dockerfile           # Multi-Stage: Node-Build → nginx
│   └── nginx.conf
├── docker-compose.yml
└── .gitignore
```

## Bekannte Grenzen / Roadmap

- [ ] **Auto-Re-Index** — derzeit muss `index.py` nach Vault-Änderungen manuell laufen
- [ ] **Antwort-Streaming** — Antwort erscheint erst komplett (kein Token-Stream)
- [ ] **Konversations-Gedächtnis** — jede Frage ist isoliert, Verlauf wird nicht mitgeschickt
- [ ] **CORS einschränken** — aktuell `allow_origins=["*"]`, vor echtem Deploy begrenzen
- [ ] **Header-basiertes Chunking** — derzeit feste Zeichengrenzen statt Schnitt an `##`

## Status

MVP funktionsfähig — Indexierung, Chat-Endpoint, Frontend und Docker-Compose-Setup lokal getestet. Repository live: [github.com/gerdam/vault-rag-chatbot](https://github.com/gerdam/vault-rag-chatbot). Offen: Screenshot/Demo fürs Portfolio.

## Lizenz

Privates Lernprojekt.
