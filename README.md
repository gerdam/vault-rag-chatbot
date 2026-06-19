# Vault-RAG-Chatbot

Ein RAG-Chatbot (Retrieval-Augmented Generation), der Fragen **ausschließlich
auf Basis eines persönlichen Obsidian-Vaults** beantwortet. Frage stellen →
relevante Notiz-Ausschnitte werden aus einer Vektordatenbank geholt → Claude
formuliert daraus eine Antwort **mit Quellenangabe**.

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

## Bekannte Grenzen / Roadmap

- [ ] **Auto-Re-Index** — derzeit muss `index.py` nach Vault-Änderungen manuell laufen
- [ ] **Antwort-Streaming** — Antwort erscheint erst komplett (kein Token-Stream)
- [ ] **Konversations-Gedächtnis** — jede Frage ist isoliert, Verlauf wird nicht mitgeschickt
- [ ] **CORS einschränken** — aktuell `allow_origins=["*"]`, vor echtem Deploy begrenzen
- [ ] **Header-basiertes Chunking** — derzeit feste Zeichengrenzen statt Schnitt an `##`

## Lizenz

Privates Lernprojekt.
