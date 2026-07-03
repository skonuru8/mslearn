import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import type { ConceptDetail, TeachResponse } from "../api/types";
import { MarkdownWithCitations } from "../components/MarkdownWithCitations";
import { ErrorBanner, Loading } from "../components/Status";
import { splitTeachMarkdown } from "../utils/teachMarkdown";

export function ConceptView() {
  const { id = "" } = useParams();
  const [detail, setDetail] = useState<ConceptDetail | null>(null);
  const [markdown, setMarkdown] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
      setMarkdown(teach.markdown);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load concept");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    setDetail(null);
    setMarkdown("");
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

  if (!detail) {
    if (loading) {
      return <Loading />;
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

  const { main, tension } = splitTeachMarkdown(markdown);

  return (
    <section className="panel">
      <h1>{detail.concept.name}</h1>
      <p>{detail.concept.summary}</p>
      <ErrorBanner message={error} />
      <button type="button" onClick={() => void load(true)}>
        Regenerate teaching
      </button>

      <h2>Teaching</h2>
      <MarkdownWithCitations text={main} citations={detail.citations} />
      {tension ? (
        <div className="tension">
          <MarkdownWithCitations text={tension} citations={detail.citations} />
        </div>
      ) : null}

      <h2>Claims</h2>
      {detail.claims.map((claim) => (
        <div key={claim.claim_id} className="claim-row">
          <span>
            [{claim.claim_id}] {claim.text}
          </span>
          <button type="button" onClick={() => void onFlag(claim.claim_id)}>
            Flag
          </button>
        </div>
      ))}
    </section>
  );
}
