import type { FormEvent } from "react";
import { useEffect, useMemo, useState } from "react";
import { streamChat } from "../api/client";
import { ErrorBanner } from "../components/Status";

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  citations?: string[];
};

const SESSION_KEY = "mslearn.chat.session";

function sessionId(): string {
  const existing = sessionStorage.getItem(SESSION_KEY);
  if (existing) {
    return existing;
  }
  const created = crypto.randomUUID();
  sessionStorage.setItem(SESSION_KEY, created);
  return created;
}

export function ChatView() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sid = useMemo(() => sessionId(), []);

  useEffect(() => {
    void (async () => {
      try {
        const response = await fetch(`/api/chat/sessions/${encodeURIComponent(sid)}`);
        if (!response.ok) {
          return;
        }
        const body = (await response.json()) as { turns?: Array<{ question: string; answer: string }> };
        const restored: ChatMessage[] = [];
        for (const turn of body.turns ?? []) {
          restored.push({ role: "user", content: turn.question });
          restored.push({ role: "assistant", content: turn.answer });
        }
        setMessages(restored.slice(-20));
      } catch {
        // transient history optional
      }
    })();
  }, [sid]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const question = input.trim();
    if (!question || streaming) {
      return;
    }
    setInput("");
    setError(null);
    setStreaming(true);
    setMessages((prev) => [...prev, { role: "user", content: question }, { role: "assistant", content: "" }]);

    try {
      await streamChat(
        question,
        sid,
        (delta) => {
          setMessages((prev) => {
            const copy = [...prev];
            const last = copy[copy.length - 1];
            if (last?.role === "assistant") {
              copy[copy.length - 1] = { ...last, content: last.content + delta };
            }
            return copy;
          });
        },
        (citations) => {
          setMessages((prev) => {
            const copy = [...prev];
            const last = copy[copy.length - 1];
            if (last?.role === "assistant") {
              copy[copy.length - 1] = { ...last, citations };
            }
            return copy.slice(-20);
          });
        },
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Chat failed");
      setMessages((prev) => prev.slice(0, -1));
    } finally {
      setStreaming(false);
    }
  }

  return (
    <section className="panel">
      <h1>Chat</h1>
      <ErrorBanner message={error} />
      <div className="chat-log">
        {messages.map((message, index) => (
          <div key={index} className={`chat-bubble ${message.role}`}>
            <div>{message.content || (streaming && message.role === "assistant" ? "…" : "")}</div>
            {message.citations?.length ? (
              <p style={{ marginTop: "0.5rem" }}>
                {message.citations.map((id) => (
                  <span key={id} className="citation-chip">
                    {id}
                  </span>
                ))}
              </p>
            ) : null}
          </div>
        ))}
      </div>
      <form className="form-grid" onSubmit={(event) => void onSubmit(event)}>
        <label>
          Question
          <textarea rows={3} value={input} onChange={(event) => setInput(event.target.value)} disabled={streaming} />
        </label>
        <button type="submit" className="primary" disabled={streaming || !input.trim()}>
          Send
        </button>
      </form>
    </section>
  );
}
