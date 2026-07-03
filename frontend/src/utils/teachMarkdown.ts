export const TENSION_HEADING = "## Where sources disagree";

export function splitTeachMarkdown(markdown: string): {
  main: string;
  tension: string | null;
} {
  const index = markdown.indexOf(TENSION_HEADING);
  if (index === -1) {
    return { main: markdown, tension: null };
  }
  return {
    main: markdown.slice(0, index).trimEnd(),
    tension: markdown.slice(index).trim(),
  };
}
