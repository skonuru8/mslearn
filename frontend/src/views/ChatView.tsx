import type { FormEvent } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, streamChat } from "../api/client";
import { getActiveProjectId } from "../api/projectId";
import type { SourceRow } from "../api/types";
import { useProject } from "../context/ProjectContext";
import { ErrorBanner } from "../components/Status";
import { translateError } from "../utils/userMessages";

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  citations?: string[];
};

function sessionStorageKey(projectId: string): string {
  return `mslearn.chat.session.${projectId}`;
}

function sessionId(projectId: string): string {
  const key = sessionStorageKey(projectId);
  const existing = sessionStorage.getItem(key);
  if (existing) {
    return existing;
  }
  const created = crypto.randomUUID();
  sessionStorage.setItem(key, created);
  return created;
}

export function ChatView() {
  const { projectId } = useProject();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasMaterials, setHasMaterials] = useState<boolean | null>(null);
  const sid = useMemo(() => sessionId(projectId), [projectId]);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    setMessages([]);
    void (async () => {
      try {
        const sources = await api<SourceRow[]>("/api/corpus/sources");
        setHasMaterials(sources.some((row) => row.status === "done" || row.done_chunks > 0));
      } catch {
        setHasMaterials(null);
      }
      try {
        const response = await fetch(`/api/chat/sessions/${encodeURIComponent(sid)}`, {
          headers: { "X-Project-Id": getActiveProjectId() },
        });
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
  }, [sid, projectId]);

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

    const controller = new AbortController();
    abortRef.current = controller;
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
        controller.signal,
      );
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        return;
      }
      const raw = err instanceof Error ? err.message : "Chat failed";
      setError(translateError(raw).message);
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last?.role === "assistant" && last.content === "") {
          return prev.slice(0, -1);
        }
        return prev;
      });
    } finally {
      setStreaming(false);
    }
  }

  return (
    <section className="panel">
      <h1>Ask questions</h1>
      <ErrorBanner message={error} />
      {hasMaterials === false ? (
        <div className="onboarding-card">
          <h2>Add something to read first</h2>
          <p className="hint">
            Questions work best after you add a book or video and it finishes reading.
          </p>
          <ol className="onboarding-steps">
            <li>Add material in <Link to="/corpus">My materials</Link></li>
            <li>Wait for &ldquo;Ready to study&rdquo;</li>
            <li>Ask anything about what you uploaded</li>
          </ol>
          <Link to="/corpus" className="button-link primary">
            Add learning material
          </Link>
        </div>
      ) : null}
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
          Your question
          <textarea
            rows={3}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            disabled={streaming}
            placeholder="Ask about something in your materials…"
          />
        </label>
        <button type="submit" className="primary" disabled={streaming || !input.trim()}>
          Send
        </button>
      </form>
    </section>
  );
}
