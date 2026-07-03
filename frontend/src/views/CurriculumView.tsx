import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { ConceptMeta } from "../api/types";
import { ErrorBanner, Loading } from "../components/Status";

export function CurriculumView() {
  const [concepts, setConcepts] = useState<ConceptMeta[]>([]);
  const [conflictCounts, setConflictCounts] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const rows = await api<ConceptMeta[]>("/api/study/curriculum");
        setConcepts(rows);
        const counts: Record<string, number> = {};
        await Promise.all(
          rows.map(async (concept) => {
            const detail = await api<{ conflicts: unknown[] }>(
              `/api/study/concepts/${encodeURIComponent(concept.concept_id)}`,
            );
            counts[concept.concept_id] = detail.conflicts.length;
          }),
        );
        setConflictCounts(counts);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load curriculum");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) {
    return <Loading />;
  }

  return (
    <section className="panel">
      <h1>Curriculum</h1>
      <ErrorBanner message={error} />
      <ul className="concept-list">
        {concepts.map((concept) => (
          <li key={concept.concept_id}>
            <Link to={`/concepts/${concept.concept_id}`}>
              <strong>{concept.order_index ?? "—"}. {concept.name}</strong>
              <div>{concept.summary}</div>
              {(conflictCounts[concept.concept_id] ?? 0) > 0 ? (
                <span className="badge">{conflictCounts[concept.concept_id]} conflicts</span>
              ) : null}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
