import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
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
    try {
      const [conceptDetail, teach] = await Promise.all([
        api<ConceptDetail>(`/api/study/concepts/${encodeURIComponent(id)}`),
        api<TeachResponse>(
          `/api/study/concepts/${encodeURIComponent(id)}/teach${force ? "?force=true" : ""}`,
        ),
      ]);
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

  if (loading || !detail) {
    return <Loading />;
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
          <ReactMarkdown>{tension}</ReactMarkdown>
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
