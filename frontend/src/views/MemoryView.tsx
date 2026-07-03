import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { MemoryItem, MemoryListResponse } from "../api/types";
import { ErrorBanner, Loading } from "../components/Status";

export function MemoryView() {
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await api<MemoryListResponse>("/api/memory");
      setItems(response.items);
      setUnavailable(false);
      setError(null);
    } catch (err) {
      if (err instanceof Error && "status" in err && (err as { status: number }).status === 503) {
        setUnavailable(true);
        setItems([]);
        setError(null);
      } else {
        setError(err instanceof Error ? err.message : "Failed to load memory");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function onDelete(memoryId: string) {
    try {
      await api(`/api/memory/${encodeURIComponent(memoryId)}`, { method: "DELETE" });
      setItems((prev) => prev.filter((item) => item.memory_id !== memoryId));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  if (loading) {
    return <Loading />;
  }

  if (unavailable) {
    return (
      <section className="panel">
        <h1>Memory</h1>
        <p className="empty-state">Memory unavailable — mem0 not installed or failed to initialize.</p>
      </section>
    );
  }

  const grouped = items.reduce<Record<string, MemoryItem[]>>((acc, item) => {
    acc[item.category] = acc[item.category] ?? [];
    acc[item.category].push(item);
    return acc;
  }, {});

  return (
    <section className="panel">
      <h1>Memory</h1>
      <ErrorBanner message={error} />
      {Object.keys(grouped).length === 0 ? (
        <p className="empty-state">No learner memory items yet.</p>
      ) : (
        Object.entries(grouped).map(([category, rows]) => (
          <div key={category} className="memory-group">
            <h3>{category}</h3>
            {rows.map((item) => (
              <div key={item.memory_id} className="memory-item">
                <span>{item.text}</span>
                <button type="button" onClick={() => void onDelete(item.memory_id)}>
                  Delete
                </button>
              </div>
            ))}
          </div>
        ))
      )}
    </section>
  );
}
