import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import type { CitationRow } from "../api/types";
import { citationMap, renderWithCitations } from "./citations";

type Props = {
  text: string;
  citations?: CitationRow[];
};

function CitationBody({ text, citations = [] }: Props) {
  const map = citationMap(citations);
  const parts = renderWithCitations(text, map);
  return (
    <>
      {parts.map((part, index) =>
        typeof part === "string" ? (
          <span key={index}>{part}</span>
        ) : (
          <sup
            key={index}
            className="citation-chip"
            title={part.tooltip}
            aria-label={`citation ${part.claimId}`}
          >
            {part.claimId}
          </sup>
        ),
      )}
    </>
  );
}

function mapChildren(children: ReactNode, citations: CitationRow[]): ReactNode {
  if (typeof children === "string") {
    return <CitationBody text={children} citations={citations} />;
  }
  if (Array.isArray(children)) {
    return children.map((child, index) =>
      typeof child === "string" ? (
        <CitationBody key={index} text={child} citations={citations} />
      ) : (
        child
      ),
    );
  }
  return children;
}

export function MarkdownWithCitations({ text, citations = [] }: Props) {
  return (
    <ReactMarkdown
      components={{
        p: ({ children }) => <p>{mapChildren(children, citations)}</p>,
        li: ({ children }) => <li>{mapChildren(children, citations)}</li>,
      }}
    >
      {text}
    </ReactMarkdown>
  );
}

export function TextWithCitations({ text, citations = [] }: Props) {
  const map = citationMap(citations);
  const parts = renderWithCitations(text, map);
  return (
    <p>
      {parts.map((part, index) =>
        typeof part === "string" ? (
          <span key={index}>{part}</span>
        ) : (
          <sup key={index} className="citation-chip" title={part.tooltip}>
            {part.claimId}
          </sup>
        ),
      )}
    </p>
  );
}
