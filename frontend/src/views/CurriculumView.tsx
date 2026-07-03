import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { ConceptMeta } from "../api/types";
import { useProject } from "../context/ProjectContext";
import { ErrorBanner, Loading } from "../components/Status";

export function CurriculumView() {
  const { projectId } = useProject();
  const [concepts, setConcepts] = useState<ConceptMeta[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      setLoading(true);
      try {
        const rows = await api<ConceptMeta[]>("/api/study/curriculum");
        setConcepts(rows);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load your course");
      } finally {
        setLoading(false);
      }
    })();
  }, [projectId]);

  if (loading) {
    return <Loading />;
  }

  return (
    <section className="panel">
      <h1>My course</h1>
      <ErrorBanner message={error} />
      {concepts.length === 0 ? (
        <div className="onboarding-card">
          <h2>Your course will appear here</h2>
          <ol className="onboarding-steps">
            <li>Add a book, PDF, or video in <Link to="/corpus">My materials</Link></li>
            <li>Wait until it says &ldquo;Ready to study&rdquo;</li>
            <li>Come back here — topics from your reading show up automatically</li>
          </ol>
          <Link to="/corpus" className="button-link primary">
            Add learning material
          </Link>
        </div>
      ) : (
        <ul className="concept-list">
          {concepts.map((concept) => (
            <li key={concept.concept_id}>
              <Link to={`/concepts/${concept.concept_id}`}>
                <strong>
                  {concept.order_index ?? "—"}. {concept.name}
                </strong>
                <div>{concept.summary}</div>
                {(concept.conflict_count ?? 0) > 0 ? (
                  <span className="badge">{concept.conflict_count} different views</span>
                ) : null}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
