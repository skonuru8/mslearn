import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { MemoryItem, MemoryListResponse } from "../api/types";
import { ErrorBanner, Loading } from "../components/Status";

export function MemoryView() {
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [unavailableReason, setUnavailableReason] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await api<MemoryListResponse>("/api/memory");
      setItems(response.items);
      setUnavailable(false);
      setUnavailableReason(null);
      setError(null);
    } catch (err) {
      if (err instanceof Error && "status" in err && (err as { status: number }).status === 503) {
        setUnavailable(true);
        setUnavailableReason(err.message || null);
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
        <p className="empty-state">
          Personal memory is off. The app can still teach and quiz you — it just won&apos;t personalize.
        </p>
        {unavailableReason ? (
          <details className="technical-details">
            <summary>Show technical details</summary>
            <p>{unavailableReason}</p>
          </details>
        ) : null}
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
