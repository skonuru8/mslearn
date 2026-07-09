import { useEffect, useMemo, useState } from "react";
import type { CitationRow, Disagreement, GuideItem, GuideSection, StudyGuide } from "../api/types";

type Props = {
  guide: StudyGuide;
  progress: Record<string, boolean>;
  citations: CitationRow[];
  onToggleSection: (sectionId: string, reviewed: boolean) => void;
};

const KIND_LABELS: Record<string, string> = {
  definition: "Definition",
  claim: "Claim",
  mechanism: "Mechanism",
  example: "Example",
  caveat: "Caveat",
  actionable: "Actionable",
};

function kindClass(kind: string): string {
  return `kind-${Object.prototype.hasOwnProperty.call(KIND_LABELS, kind) ? kind : "claim"}`;
}

/** Assigns each claim id the next free number, in first-appearance order. Scope is local to a call site (per section/tl_dr/disagreement) by design. */
export function buildClaimNumbers(claimLists: string[][]): Map<string, number> {
  const map = new Map<string, number>();
  let next = 1;
  for (const claims of claimLists) {
    for (const claimId of claims) {
      if (!map.has(claimId)) {
        map.set(claimId, next);
        next += 1;
      }
    }
  }
  return map;
}

function citationLocator(row: CitationRow | undefined): string {
  if (!row) {
    return "";
  }
  const parts: string[] = [];
  if (row.page != null) {
    parts.push(`p. ${row.page}`);
  }
  if (row.start_s != null && row.end_s != null) {
    parts.push(`${row.start_s}s–${row.end_s}s`);
  }
  if (row.page == null && row.start_s == null && row.seq != null) {
    parts.push(`chunk ${row.seq}`);
  }
  if (row.href) {
    parts.push(row.href);
  } else if (row.url) {
    parts.push(row.url);
  }
  return parts.join(" · ");
}

export function ClaimText({
  text,
  claims,
  numberMap,
}: {
  text: string;
  claims: string[];
  numberMap: Map<string, number>;
}) {
  return (
    <>
      {text}
      {claims.map((claimId) => (
        <sup key={claimId} className="guide-cite" aria-label={`citation ${numberMap.get(claimId) ?? "?"}`}>
          {numberMap.get(claimId) ?? "?"}
        </sup>
      ))}
    </>
  );
}

export function SourcesFooter({
  numberMap,
  citations,
}: {
  numberMap: Map<string, number>;
  citations: CitationRow[];
}) {
  if (numberMap.size === 0) {
    return null;
  }
  const byId = new Map(citations.map((row) => [row.claim_id, row] as const));
  const entries = [...numberMap.entries()].sort((a, b) => a[1] - b[1]);
  return (
    <details className="guide-sources">
      <summary>Sources</summary>
      <ol>
        {entries.map(([claimId, number]) => {
          const row = byId.get(claimId);
          const locator = citationLocator(row);
          return (
            <li key={claimId} value={number}>
              {row?.quote ? (
                <span className="guide-quote">&ldquo;{row.quote}&rdquo;</span>
              ) : (
                <span className="guide-quote guide-quote-missing">Source unavailable</span>
              )}
              {locator ? <span className="guide-locator"> &mdash; {locator}</span> : null}
            </li>
          );
        })}
      </ol>
    </details>
  );
}

function GuideItemRow({ item, numberMap }: { item: GuideItem; numberMap: Map<string, number> }) {
  return (
    <div className={`guide-item ${kindClass(item.kind)}`}>
      <span className="guide-item-kind">{KIND_LABELS[item.kind] ?? item.kind}</span>
      <span className="guide-item-text">
        <ClaimText text={item.text} claims={item.claims} numberMap={numberMap} />
      </span>
    </div>
  );
}

function SectionCard({
  section,
  reviewed,
  citations,
  expanded,
  onToggleExpand,
  onToggleReviewed,
}: {
  section: GuideSection;
  reviewed: boolean;
  citations: CitationRow[];
  expanded: boolean;
  onToggleExpand: () => void;
  onToggleReviewed: (next: boolean) => void;
}) {
  const numberMap = useMemo(
    () => buildClaimNumbers(section.items.map((item) => item.claims)),
    [section.items],
  );
  return (
    <article
      id={`guide-section-${section.id}`}
      className={`guide-card ${expanded ? "is-expanded" : "is-collapsed"} ${reviewed ? "is-reviewed" : ""}`}
    >
      <header className="guide-card-header">
        <button
          type="button"
          className="guide-card-toggle"
          onClick={onToggleExpand}
          aria-expanded={expanded}
        >
          <span className="guide-chevron" aria-hidden="true">
            &#9662;
          </span>
          <h3>{section.title}</h3>
        </button>
        <label className="guide-reviewed">
          <input
            type="checkbox"
            checked={reviewed}
            onChange={(event) => onToggleReviewed(event.target.checked)}
          />
          Reviewed
        </label>
      </header>
      <div className="guide-card-body-wrap">
        <div className="guide-card-body">
          {section.items.map((item, index) => (
            <GuideItemRow key={index} item={item} numberMap={numberMap} />
          ))}
          <SourcesFooter numberMap={numberMap} citations={citations} />
        </div>
      </div>
    </article>
  );
}

function DisagreementCard({
  disagreement,
  citations,
}: {
  disagreement: Disagreement;
  citations: CitationRow[];
}) {
  const numberMap = useMemo(
    () => buildClaimNumbers([disagreement.a.claims, disagreement.b.claims]),
    [disagreement.a.claims, disagreement.b.claims],
  );
  return (
    <article className="guide-disagreement">
      <header className="guide-disagreement-header">
        <span className="guide-disagreement-badge">{disagreement.classification.replace(/_/g, " ")}</span>
        <p>{disagreement.summary}</p>
      </header>
      <div className="guide-compare">
        <div className="guide-compare-side guide-compare-a">
          <h4>{disagreement.a.label}</h4>
          <p>
            <ClaimText text={disagreement.a.text} claims={disagreement.a.claims} numberMap={numberMap} />
          </p>
        </div>
        <div className="guide-compare-side guide-compare-b">
          <h4>{disagreement.b.label}</h4>
          <p>
            <ClaimText text={disagreement.b.text} claims={disagreement.b.claims} numberMap={numberMap} />
          </p>
        </div>
      </div>
      <SourcesFooter numberMap={numberMap} citations={citations} />
    </article>
  );
}

/** Highlights the top-most section currently intersecting the viewport; a no-op where IntersectionObserver is unavailable (e.g. jsdom in tests). */
function useActiveSection(sectionIds: string[]): string {
  const [active, setActive] = useState(sectionIds[0] ?? "");

  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") {
      return;
    }
    const elements = sectionIds
      .map((id) => document.getElementById(`guide-section-${id}`))
      .filter((el): el is HTMLElement => el !== null);
    if (elements.length === 0) {
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((entry) => entry.isIntersecting);
        if (visible.length === 0) {
          return;
        }
        const topMost = visible.reduce((a, b) =>
          a.boundingClientRect.top <= b.boundingClientRect.top ? a : b,
        );
        setActive(topMost.target.id.replace("guide-section-", ""));
      },
      { rootMargin: "-15% 0px -70% 0px" },
    );
    elements.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [sectionIds]);

  return active;
}

export function InteractiveGuide({ guide, progress, citations, onToggleSection }: Props) {
  const sectionIds = useMemo(() => guide.sections.map((s) => s.id), [guide.sections]);
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set(sectionIds));
  const active = useActiveSection(sectionIds);
  const tlDrNumberMap = useMemo(() => buildClaimNumbers([guide.tl_dr.claims]), [guide.tl_dr.claims]);
  const sectionByTitle = useMemo(
    () => new Map(guide.sections.map((section) => [section.title, section] as const)),
    [guide.sections],
  );

  const reviewedCount = sectionIds.filter((id) => progress[id]).length;
  const total = sectionIds.length;

  function toggleExpand(id: string) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  function jumpTo(id: string) {
    document.getElementById(`guide-section-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <div className="interactive-guide">
      <aside className="guide-minimap">
        <div className="guide-progress" aria-live="polite">
          <div className="guide-progress-track">
            <div
              className="guide-progress-fill"
              style={{ width: total ? `${(reviewedCount / total) * 100}%` : "0%" }}
            />
          </div>
          <p className="guide-progress-label">
            {reviewedCount} / {total} reviewed
          </p>
        </div>
        <nav aria-label="Section outline">
          <ol className="guide-minimap-list">
            {guide.skeleton.map((title, index) => {
              const section = sectionByTitle.get(title);
              const isActive = Boolean(section && section.id === active);
              const isReviewed = Boolean(section && progress[section.id]);
              return (
                <li key={`${title}-${index}`}>
                  {section ? (
                    <button
                      type="button"
                      className={`guide-minimap-link ${isActive ? "is-active" : ""} ${isReviewed ? "is-reviewed" : ""}`}
                      onClick={() => jumpTo(section.id)}
                    >
                      {title}
                    </button>
                  ) : (
                    <span className="guide-minimap-label">{title}</span>
                  )}
                </li>
              );
            })}
          </ol>
        </nav>
      </aside>

      <div className="guide-main">
        <div className="guide-tldr">
          <span className="guide-tldr-label">TL;DR</span>
          <p className="guide-tldr-text">
            <ClaimText text={guide.tl_dr.text} claims={guide.tl_dr.claims} numberMap={tlDrNumberMap} />
          </p>
          <SourcesFooter numberMap={tlDrNumberMap} citations={citations} />
        </div>

        <div className="guide-sections">
          {guide.sections.map((section) => (
            <SectionCard
              key={section.id}
              section={section}
              reviewed={Boolean(progress[section.id])}
              citations={citations}
              expanded={expandedIds.has(section.id)}
              onToggleExpand={() => toggleExpand(section.id)}
              onToggleReviewed={(next) => onToggleSection(section.id, next)}
            />
          ))}
        </div>

        {guide.disagreements.length > 0 ? (
          <div className="guide-disagreements">
            <h3>Where sources disagree</h3>
            {guide.disagreements.map((disagreement, index) => (
              <DisagreementCard key={index} disagreement={disagreement} citations={citations} />
            ))}
          </div>
        ) : null}

        {guide.open_questions.length > 0 ? (
          <div className="guide-open-questions">
            <h3>Open questions</h3>
            <ul>
              {guide.open_questions.map((question, index) => (
                <li key={index}>{question}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </div>
  );
}
