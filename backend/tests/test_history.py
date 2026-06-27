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
