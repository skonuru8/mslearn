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

export function MarkdownWithCitations({ text, citations = [] }: Props) {
  return (
    <ReactMarkdown
      components={{
        p: ({ children }) => (
          <p>
            {typeof children === "string" ? (
              <CitationBody text={children} citations={citations} />
            ) : (
              children
            )}
          </p>
        ),
        li: ({ children }) => (
          <li>
            {typeof children === "string" ? (
              <CitationBody text={children} citations={citations} />
            ) : (
              children
            )}
          </li>
        ),
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
