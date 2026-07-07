import type { ChangeEvent } from "react";
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type {
  ConceptDetail,
  FlashcardRow,
  SelfCheckRow,
  StudyGuide,
  TeachResponse,
} from "../api/types";
import { buildClaimNumbers, ClaimText, InteractiveGuide, SourcesFooter } from "../components/InteractiveGuide";
import { ErrorBanner, Loading } from "../components/Status";

export function ConceptView() {
  const { id = "" } = useParams();
  const [detail, setDetail] = useState<ConceptDetail | null>(null);
  const [guide, setGuide] = useState<StudyGuide | null>(null);
  const [progress, setProgress] = useState<Record<string, boolean>>({});
  const [cached, setCached] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notInProject, setNotInProject] = useState(false);

  const [practiceCount, setPracticeCount] = useState("3");
  const [flashcards, setFlashcards] = useState<FlashcardRow[] | null>(null);
  const [flippedIndexes, setFlippedIndexes] = useState<Set<number>>(new Set());
  const [flashcardsLoading, setFlashcardsLoading] = useState(false);
  const [flashcardsError, setFlashcardsError] = useState<string | null>(null);

  const [checks, setChecks] = useState<SelfCheckRow[] | null>(null);
  const [selfCheckLoading, setSelfCheckLoading] = useState(false);
  const [selfCheckError, setSelfCheckError] = useState<string | null>(null);

  const load = useCallback(async (force = false) => {
    setLoading(true);
    const requestedId = id;
    try {
      const [conceptDetail, teach] = await Promise.all([
        api<ConceptDetail>(`/api/study/concepts/${encodeURIComponent(id)}`),
        api<TeachResponse>(
          `/api/study/concepts/${encodeURIComponent(id)}/teach${force ? "?force=true" : ""}`,
        ),
      ]);
      if (requestedId !== id) {
        return; // stale response after navigation
      }
      setDetail(conceptDetail);
      setGuide(teach.guide);
      setProgress(teach.progress);
      setCached(Boolean(teach.cached));
      setError(null);
      setNotInProject(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setNotInProject(true);
        setError(null);
      } else {
        setError(err instanceof Error ? err.message : "Failed to load concept");
      }
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    setDetail(null);
    setGuide(null);
    setProgress({});
    setCached(false);
    setNotInProject(false);
    setFlashcards(null);
    setChecks(null);
    setFlippedIndexes(new Set());
    void load();
  }, [load]);

  async function onFlag(claimId: string) {
    const reason = window.prompt("Reason for flagging this claim?");
    if (!reason) {
      return;
    }
    try {
      await api(`/api/study/claims/${encodeURIComponent(claimId)}/flag`, {
        method: "POST",
        body: JSON.stringify({ reason }),
      });
      await load(true);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Flag failed");
    }
  }

  async function toggleSection(sectionId: string, reviewed: boolean) {
    const previous = progress;
    setProgress((prev) => ({ ...prev, [sectionId]: reviewed }));
    try {
      const result = await api<{ progress: Record<string, boolean> }>(
        `/api/study/concepts/${encodeURIComponent(id)}/progress`,
        { method: "POST", body: JSON.stringify({ section_id: sectionId, reviewed }) },
      );
      setProgress(result.progress);
    } catch (err) {
      setProgress(previous);
      setError(err instanceof Error ? err.message : "Could not save progress");
    }
  }

  function parsedCount(): number {
    const parsed = Number.parseInt(practiceCount, 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 5;
  }

  async function makeFlashcards() {
    setFlashcardsLoading(true);
    setFlashcardsError(null);
    setFlippedIndexes(new Set());
    try {
      const result = await api<{ cards: FlashcardRow[] }>(
        `/api/study/concepts/${encodeURIComponent(id)}/flashcards`,
        { method: "POST", body: JSON.stringify({ count: parsedCount() }) },
      );
      setFlashcards(result.cards);
    } catch (err) {
      setFlashcardsError(err instanceof Error ? err.message : "Could not build flashcards");
    } finally {
      setFlashcardsLoading(false);
    }
  }

  async function makeSelfCheck() {
    setSelfCheckLoading(true);
    setSelfCheckError(null);
    try {
      const result = await api<{ checks: SelfCheckRow[] }>(
        `/api/study/concepts/${encodeURIComponent(id)}/selfcheck`,
        { method: "POST", body: JSON.stringify({ count: parsedCount() }) },
      );
      setChecks(result.checks);
    } catch (err) {
      setSelfCheckError(err instanceof Error ? err.message : "Could not build self-check questions");
    } finally {
      setSelfCheckLoading(false);
    }
  }

  function toggleFlip(index: number) {
    setFlippedIndexes((prev) => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  }

  function onCountChange(event: ChangeEvent<HTMLInputElement>) {
    setPracticeCount(event.target.value);
  }

  if (!detail) {
    if (loading) {
      return <Loading label="Writing your lesson… (first time can take a minute or two)" />;
    }
    if (notInProject) {
      return (
        <section className="panel">
          <h1>Topic not in this project</h1>
          <p>This topic is not part of this project.</p>
          <Link to="/curriculum">Go to this project’s course</Link>
        </section>
      );
    }
    return (
      <section className="panel">
        <h1>Concept</h1>
        <ErrorBanner message={error ?? "Concept failed to load"} />
        <button type="button" onClick={() => void load()}>
          Retry
        </button>
      </section>
    );
  }

  return (
    <section className="panel">
      <h1>{detail.concept.name}</h1>
      <p>{detail.concept.summary}</p>
      <ErrorBanner message={error} />
      <button type="button" onClick={() => void load(true)} disabled={loading}>
        Regenerate teaching
      </button>

      <h2>Teaching</h2>
      {loading ? (
        <Loading label="Writing your lesson… (first time can take a minute or two)" />
      ) : (
        <>
          {cached ? <p className="cached-badge">Loaded instantly from the saved lesson.</p> : null}
          {guide ? (
            <InteractiveGuide
              guide={guide}
              progress={progress}
              citations={detail.citations}
              onToggleSection={(sectionId, reviewed) => void toggleSection(sectionId, reviewed)}
            />
          ) : null}
        </>
      )}

      <div className="study-extras">
        <h2>Practice</h2>
        <div className="study-extras-controls">
          <label htmlFor="practice-count">Count</label>
          <input
            id="practice-count"
            type="number"
            min={1}
            max={20}
            value={practiceCount}
            onChange={onCountChange}
          />
          <button type="button" onClick={() => void makeFlashcards()} disabled={flashcardsLoading}>
            {flashcardsLoading ? "Making flashcards…" : "Make flashcards"}
          </button>
          <button type="button" onClick={() => void makeSelfCheck()} disabled={selfCheckLoading}>
            {selfCheckLoading ? "Checking…" : "Self-check"}
          </button>
        </div>
        <ErrorBanner message={flashcardsError} />
        <ErrorBanner message={selfCheckError} />

        {flashcards ? (
          flashcards.length > 0 ? (
            <div className="flashcard-grid">
              {flashcards.map((card, index) => {
                const numberMap = buildClaimNumbers([card.claims]);
                const flipped = flippedIndexes.has(index);
                return (
                  <button
                    type="button"
                    key={index}
                    className={`flashcard ${flipped ? "is-flipped" : ""}`}
                    onClick={() => toggleFlip(index)}
                    aria-pressed={flipped}
                  >
                    <span className="flashcard-inner">
                      <span className="flashcard-face flashcard-face-front">
                        <ClaimText text={card.front} claims={card.claims} numberMap={numberMap} />
                      </span>
                      <span className="flashcard-face flashcard-face-back">
                        <ClaimText text={card.back} claims={card.claims} numberMap={numberMap} />
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          ) : (
            <p className="empty-state">No grounded flashcards available yet.</p>
          )
        ) : null}

        {checks ? (
          checks.length > 0 ? (
            <div className="selfcheck-list">
              {checks.map((check, index) => {
                const numberMap = buildClaimNumbers([check.claims]);
                return (
                  <details key={index} className="selfcheck-item">
                    <summary>{check.question}</summary>
                    <div className="selfcheck-answer">
                      <ClaimText text={check.answer} claims={check.claims} numberMap={numberMap} />
                      <SourcesFooter numberMap={numberMap} citations={detail.citations} />
                    </div>
                  </details>
                );
              })}
            </div>
          ) : (
            <p className="empty-state">No grounded self-check questions available yet.</p>
          )
        ) : null}
      </div>

      <h2>Claims</h2>
      {detail.claims.map((claim) => (
        <div key={claim.claim_id} className="claim-row">
          <span>
            [{claim.claim_id}] {claim.text}
            {claim.trust === "image_observed" ? (
              <span className="badge image-badge" title="Read from an image by a vision model, not a verbatim text quote">
                {" "}from image
              </span>
            ) : null}
          </span>
          <button type="button" onClick={() => void onFlag(claim.claim_id)}>
            Flag
          </button>
        </div>
      ))}
    </section>
  );
}
