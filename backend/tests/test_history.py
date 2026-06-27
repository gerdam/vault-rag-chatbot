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
