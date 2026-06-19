"""
index.py — Obsidian-Vault in ChromaDB einlesen.
Aufruf: python index.py
"""

import os
import sys
from pathlib import Path

import chromadb
from dotenv import load_dotenv

load_dotenv()

VAULT_PATH = os.getenv("VAULT_PATH", r"C:\Users\info\MAG\obsidian-vault")
CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_data")
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

# Ordner die übersprungen werden (Templates, Archive, System)
SKIP_DIRS = {".obsidian", ".git", "99-Templates", ".claude"}


def chunk_text(text: str) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - CHUNK_OVERLAP
    return chunks


def index_vault() -> None:
    vault = Path(VAULT_PATH)
    if not vault.exists():
        print(f"Fehler: Vault-Pfad nicht gefunden: {VAULT_PATH}")
        sys.exit(1)

    chroma = chromadb.PersistentClient(path=CHROMA_PATH)
    # Bestehende Collection löschen und neu aufbauen (sauberer Re-Index)
    try:
        chroma.delete_collection("vault")
        print("Alte Collection gelöscht.")
    except Exception:
        pass
    collection = chroma.create_collection("vault")

    md_files = [
        f for f in vault.rglob("*.md")
        if not any(skip in f.parts for skip in SKIP_DIRS)
    ]
    print(f"Gefunden: {len(md_files)} Markdown-Dateien\n")

    ids, docs, metas = [], [], []

    for md_file in md_files:
        try:
            content = md_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if len(content.strip()) < 50:
            continue

        rel_path = str(md_file.relative_to(vault))
        for i, chunk in enumerate(chunk_text(content)):
            ids.append(f"{rel_path}::{i}")
            docs.append(chunk)
            metas.append({"datei": rel_path, "chunk": i})

    # ChromaDB in Batches von 100 befüllen
    batch_size = 100
    total = len(ids)
    for i in range(0, total, batch_size):
        collection.upsert(
            ids=ids[i : i + batch_size],
            documents=docs[i : i + batch_size],
            metadatas=metas[i : i + batch_size],
        )
        print(f"  {min(i + batch_size, total)}/{total} Chunks gespeichert")

    print(f"\nFertig! {collection.count()} Chunks in ChromaDB.")


if __name__ == "__main__":
    index_vault()
