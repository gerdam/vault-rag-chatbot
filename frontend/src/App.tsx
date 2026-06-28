import { useState, useRef, useEffect } from "react";
import "./App.css";

// Basis-URL des FastAPI-Backends. Spaeter via .env (VITE_API_URL) konfigurierbar.
const API_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

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

  // Eine Session-ID pro Chat. Frontend generiert sie; Backend nutzt sie als
  // Schlüssel für den gespeicherten Verlauf. crypto.randomUUID() ist im Browser nativ.
  const sessionId = useRef(crypto.randomUUID());

  // Ref auf das Ende der Liste, um nach jeder Nachricht runterzuscrollen.
  const endeRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endeRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [verlauf]);

  async function frageSenden() {
    const frage = eingabe.trim();
    if (!frage || laedt) return;

    // User-Nachricht + leere Antwort-Blase (waechst gleich) anlegen.
    setVerlauf((v) => [
      ...v,
      { rolle: "frage", text: frage },
      { rolle: "antwort", text: "", quellen: [] },
    ]);
    setEingabe("");
    setLaedt(true);
    setFehler(null);

    // Haengt Text an die LETZTE Nachricht (die Antwort-Blase) an.
    const anAntwortAnhaengen = (text: string) =>
      setVerlauf((v) => {
        const kopie = [...v];
        const letzte = kopie[kopie.length - 1];
        kopie[kopie.length - 1] = { ...letzte, text: letzte.text + text };
        return kopie;
      });

    const setzeQuellen = (quellen: string[]) =>
      setVerlauf((v) => {
        const kopie = [...v];
        const letzte = kopie[kopie.length - 1];
        kopie[kopie.length - 1] = { ...letzte, quellen };
        return kopie;
      });

    try {
      const res = await fetch(`${API_URL}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: frage, session_id: sessionId.current }),
      });

      if (!res.ok || !res.body) {
        throw new Error(`Server antwortete mit ${res.status}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let puffer = "";

      // Stream lesen, bis er endet.
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        puffer += decoder.decode(value, { stream: true });

        // Vollstaendige SSE-Bloecke sind durch eine Leerzeile getrennt.
        const bloecke = puffer.split("\n\n");
        puffer = bloecke.pop() ?? ""; // letzter (evtl. unvollstaendiger) Block bleibt im Puffer

        for (const block of bloecke) {
          if (!block.trim()) continue;
          let eventName = "";
          let dataJson = "";
          for (const zeile of block.split("\n")) {
            if (zeile.startsWith("event:")) eventName = zeile.slice(6).trim();
            else if (zeile.startsWith("data:")) dataJson = zeile.slice(5).trim();
          }
          const daten = dataJson ? JSON.parse(dataJson) : {};

          if (eventName === "sources") setzeQuellen(daten.quellen ?? []);
          else if (eventName === "token") anAntwortAnhaengen(daten.text ?? "");
          else if (eventName === "error") throw new Error(daten.detail ?? "Stream-Fehler");
          // "done" -> Schleife endet ohnehin am Stream-Ende
        }
      }
    } catch (e) {
      setFehler(e instanceof Error ? e.message : "Unbekannter Fehler");
    } finally {
      setLaedt(false);
    }
  }

  // Startet eine frische Session: neue ID + leerer Verlauf.
  function neuerChat() {
    sessionId.current = crypto.randomUUID();
    setVerlauf([]);
    setFehler(null);
    setEingabe("");
  }

  return (
    <div className="app">
      <header>
        <h1>Vault-RAG-Chatbot</h1>
        <p className="untertitel">
          Fragen an deinen Wissens-Vault — beantwortet aus 36.227 Chunks.
        </p>
        <button className="neuer-chat" onClick={neuerChat} disabled={laedt}>
          Neuer Chat
        </button>
      </header>

      <div className="verlauf">
        {verlauf.length === 0 && (
          <p className="hinweis">Stell eine Frage zu deinen Notizen …</p>
        )}

        {verlauf.map((m, i) => (
          <div key={i} className={`nachricht ${m.rolle}`}>
            {/* Leere Antwort-Blase zeigt "…" bis das erste Token eintrifft. */}
            <div
              className={`blase${
                m.rolle === "antwort" && m.text === "" ? " tippt" : ""
              }`}
            >
              {m.rolle === "antwort" && m.text === "" ? "…" : m.text}
            </div>
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
