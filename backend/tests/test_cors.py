"""
Tests für die CORS-Härtung: Allowlist statt allow_origins=["*"].

parse_allowed_origins ist eine reine Funktion (Env-String -> Liste) und damit
ohne TestClient/App testbar. Die zwei Integration-Tests prüfen das tatsächliche
Verhalten der laufenden App: konfigurierte Origin bekommt den CORS-Header,
eine fremde Origin nicht.
"""

import main


def test_parse_allowed_origins_splittet_kommagetrennte_liste():
    assert main.parse_allowed_origins("http://a.de,http://b.de") == [
        "http://a.de",
        "http://b.de",
    ]


def test_parse_allowed_origins_ohne_env_liefert_default():
    assert main.parse_allowed_origins(None) == ["http://localhost:5173"]


def test_parse_allowed_origins_trimmt_leerzeichen():
    assert main.parse_allowed_origins(" http://a.de , http://b.de ") == [
        "http://a.de",
        "http://b.de",
    ]


def test_cors_erlaubt_konfigurierte_origin(client):
    r = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_antwortet_fremder_origin_ohne_allow_header(client):
    r = client.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in r.headers
