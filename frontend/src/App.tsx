import { useState, useRef, useEffect } from "react";
import "./App.css";

// Basis-URL des FastAPI-Backends. Spaeter via .env (VITE_API_URL) konfigurierbar.
const API_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

// --- Typen: spiegeln das Pydantic-Schema im Backend (ChatResponse) ---
interface ChatResponse {
  antwort: string;
  quellen: string[];
}

// Eine Nachricht in der Chat-Historie. "frage" = User, "antwort" = Bot.
interface Message {
  rolle: "frage" | "antwort";
  text: string;
  quellen?: string[];
}

function App() {
  // useState: Reacts Weg, veraenderlichen Zustand pro Komponente zu halten.
  const [eingabe, setEingabe] = useState("");
  const [verlauf, setVerlauf] = useState<Message[]>([]);
  const [laedt, setLaedt] = useState(false);
  const [fehler, setFehler] = useState<string | null>(null);

  // Ref auf das Ende der Liste, um nach jeder Nachricht runterzuscrollen.
  const endeRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endeRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [verlauf]);

  async function frageSenden() {
    const frage = eingabe.trim();
    if (!frage || laedt) return;

    // User-Nachricht sofort anzeigen, Eingabefeld leeren.
    setVerlauf((v) => [...v, { rolle: "frage", text: frage }]);
    setEingabe("");
    setLaedt(true);
    setFehler(null);

    try {
      // POST an das Backend. fetch ist asynchron -> await.
      const res = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: frage }),
      });

      if (!res.ok) {
        throw new Error(`Server antwortete mit ${res.status}`);
      }

      const daten: ChatResponse = await res.json();
      setVerlauf((v) => [
        ...v,
        { rolle: "antwort", text: daten.antwort, quellen: daten.quellen },
      ]);
    } catch (e) {
      setFehler(e instanceof Error ? e.message : "Unbekannter Fehler");
    } finally {
      // finally laeuft immer -> Ladezustand sicher zuruecksetzen.
      setLaedt(false);
    }
  }

  return (
    <div className="app">
      <header>
        <h1>Vault-RAG-Chatbot</h1>
        <p className="untertitel">
          Fragen an deinen Wissens-Vault — beantwortet aus 36.227 Chunks.
        </p>
      </header>

      <div className="verlauf">
        {verlauf.length === 0 && (
          <p className="hinweis">Stell eine Frage zu deinen Notizen …</p>
        )}

        {verlauf.map((m, i) => (
          <div key={i} className={`nachricht ${m.rolle}`}>
            <div className="blase">{m.text}</div>
            {m.quellen && m.quellen.length > 0 && (
              <div className="quellen">
                Quellen:{" "}
                {m.quellen.map((q) => (
                  <span key={q} className="quelle">
                    {q.split(/[\\/]/).pop()}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}

        {laedt && (
          <div className="nachricht antwort">
            <div className="blase tippt">…</div>
          </div>
        )}
        {fehler && <div className="fehler">Fehler: {fehler}</div>}
        <div ref={endeRef} />
      </div>

      <div className="eingabezeile">
        <input
          type="text"
          value={eingabe}
          placeholder="Deine Frage …"
          onChange={(e) => setEingabe(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && frageSenden()}
          disabled={laedt}
        />
        <button onClick={frageSenden} disabled={laedt || !eingabe.trim()}>
          {laedt ? "…" : "Senden"}
        </button>
      </div>
    </div>
  );
}

export default App;
