import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { NoteFeedbackRow } from "../api/types";
import { ErrorBanner } from "./Status";

type Props = { conceptId: string };

const TAG_OPTIONS: { value: string; label: string }[] = [
  { value: "too_shallow", label: "Too shallow" },
  { value: "repetitive", label: "Repetitive" },
  { value: "wrong", label: "Wrong" },
  { value: "off_topic", label: "Off-topic" },
];

export function NoteFeedback({ conceptId }: Props) {
  const [helpful, setHelpful] = useState<boolean | null>(null);
  const [tags, setTags] = useState<Set<string>>(new Set());
  const [comment, setComment] = useState("");
  const [guideHash, setGuideHash] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setHelpful(null);
    setTags(new Set());
    setComment("");
    setGuideHash(null);
    setSaved(false);
    setError(null);
    void api<Partial<NoteFeedbackRow>>(
      `/api/study/concepts/${encodeURIComponent(conceptId)}/feedback`,
    )
      .then((row) => {
        if (cancelled || Object.keys(row).length === 0) {
          return;
        }
        setHelpful(row.helpful ?? null);
        setTags(new Set(row.tags ?? []));
        setComment(row.comment ?? "");
        setGuideHash(row.guide_hash ?? null);
        setSaved(true);
      })
      .catch(() => {
        // no prior feedback, or the fetch failed — leave the form blank
      });
    return () => {
      cancelled = true;
    };
  }, [conceptId]);

  function toggleTag(tag: string) {
    setSaved(false);
    setTags((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) {
        next.delete(tag);
      } else {
        next.add(tag);
      }
      return next;
    });
  }

  function chooseHelpful(value: boolean) {
    setSaved(false);
    setHelpful((prev) => (prev === value ? null : value));
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      await api(`/api/study/concepts/${encodeURIComponent(conceptId)}/feedback`, {
        method: "POST",
        body: JSON.stringify({
          helpful,
          tags: Array.from(tags),
          comment,
          guide_hash: guideHash,
        }),
      });
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save feedback");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="note-feedback">
      <h3>Was this note helpful?</h3>
      <div className="note-feedback-thumbs">
        <button
          type="button"
          aria-pressed={helpful === true}
          className={helpful === true ? "is-active" : ""}
          onClick={() => chooseHelpful(true)}
        >
          👍
        </button>
        <button
          type="button"
          aria-pressed={helpful === false}
          className={helpful === false ? "is-active" : ""}
          onClick={() => chooseHelpful(false)}
        >
          👎
        </button>
      </div>
      <div className="note-feedback-tags">
        {TAG_OPTIONS.map((opt) => (
          <label key={opt.value} className="note-feedback-tag">
            <input
              type="checkbox"
              checked={tags.has(opt.value)}
              onChange={() => toggleTag(opt.value)}
            />
            {opt.label}
          </label>
        ))}
      </div>
      <textarea
        aria-label="Feedback comment"
        placeholder="Anything else? (optional)"
        value={comment}
        onChange={(event) => {
          setSaved(false);
          setComment(event.target.value);
        }}
      />
      <div className="note-feedback-actions">
        <button type="button" onClick={() => void save()} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
        {saved ? <span className="note-feedback-saved">Saved</span> : null}
      </div>
      <ErrorBanner message={error} />
    </div>
  );
}
