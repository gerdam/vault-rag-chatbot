# Backend-Image: FastAPI + ChromaDB-Abfrage
FROM python:3.12-slim

WORKDIR /app

# Zuerst nur requirements -> Docker-Layer-Cache: pip-install wird nur
# neu ausgefuehrt, wenn sich requirements.txt aendert.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Anwendungscode (chroma_data wird NICHT kopiert, sondern als Volume gemountet)
COPY main.py index.py history.py ./

# Im Container auf allen Interfaces lauschen (0.0.0.0), nicht nur 127.0.0.1,
# sonst ist der Port von ausserhalb des Containers nicht erreichbar.
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
