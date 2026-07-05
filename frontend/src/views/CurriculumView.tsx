import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { ConceptMeta, SynthesisProgress, SynthesisStatusResponse } from "../api/types";
import { useProject } from "../context/ProjectContext";
import { ErrorBanner, Loading } from "../components/Status";
import { formatSynthesisProgress } from "../utils/userMessages";

export function CurriculumView() {
  const { projectId } = useProject();
  const [concepts, setConcepts] = useState<ConceptMeta[]>([]);
  const [building, setBuilding] = useState(false);
  const [progress, setProgress] = useState<SynthesisProgress | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const [rows, synth] = await Promise.all([
      api<ConceptMeta[]>("/api/study/curriculum"),
      api<SynthesisStatusResponse>("/api/corpus/synthesis/status").catch(() => null),
    ]);
    setConcepts(rows);
    setBuilding(Boolean(synth?.running_since));
    setProgress(synth?.progress ?? null);
    setError(null);
  }, []);

  useEffect(() => {
    void (async () => {
      setLoading(true);
      try {
        await refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load your course");
      } finally {
        setLoading(false);
      }
    })();
  }, [refresh, projectId]);

  useEffect(() => {
    if (!building) {
      return;
    }
    // Course is being assembled right now — refresh so topics appear on
    // their own instead of requiring a manual reload.
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        void refresh().catch(() => {
          /* keep polling */
        });
      }
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [building, refresh]);

  if (loading) {
    return <Loading />;
  }

  return (
    <section className="panel">
      <h1>My course</h1>
      <ErrorBanner message={error} />
      {concepts.length === 0 && building ? (
        <div className="onboarding-card">
          <h2>Building your course…</h2>
          <p>
            {formatSynthesisProgress(progress)} This can take several minutes for a big source.
            This page refreshes itself; no need to reload.
          </p>
        </div>
      ) : concepts.length === 0 ? (
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
