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
    rows.reverse()
    return [{"role": role, "content": content} for role, content in rows]


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
