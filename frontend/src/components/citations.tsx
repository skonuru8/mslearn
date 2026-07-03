import type { CitationRow } from "../api/types";

const CLAIM_RE = /\[claim:([^\]\s]+)\]/g;

export function citationMap(citations: CitationRow[]): Map<string, CitationRow> {
  return new Map(citations.map((row) => [row.claim_id, row]));
}

export function formatCitationTooltip(citation: CitationRow): string {
  const parts = [citation.source_id];
  if (citation.kind) parts.push(citation.kind);
  if (citation.page != null) parts.push(`p.${citation.page}`);
  if (citation.seq != null) parts.push(`seq ${citation.seq}`);
  if (citation.href) parts.push(citation.href);
  if (citation.url) parts.push(citation.url);
  if (citation.start_s != null && citation.end_s != null) {
    parts.push(`${citation.start_s}-${citation.end_s}s`);
  }
  return parts.join(" · ");
}

export function renderWithCitations(
  text: string,
  citations: Map<string, CitationRow>,
): Array<string | { claimId: string; tooltip: string }> {
  const parts: Array<string | { claimId: string; tooltip: string }> = [];
  let last = 0;
  for (const match of text.matchAll(CLAIM_RE)) {
    const index = match.index ?? 0;
    if (index > last) {
      parts.push(text.slice(last, index));
    }
    const claimId = match[1];
    const row = citations.get(claimId);
    parts.push({
      claimId,
      tooltip: row ? formatCitationTooltip(row) : claimId,
    });
    last = index + match[0].length;
  }
  if (last < text.length) {
    parts.push(text.slice(last));
  }
  return parts;
}

export function countClaimCitations(text: string): number {
  return [...text.matchAll(CLAIM_RE)].length;
}
