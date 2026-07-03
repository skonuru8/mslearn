/** Plain-language labels for source pipeline statuses. */
export function sourceStatusLabel(status: string): string {
  switch (status) {
    case "running":
      return "Reading…";
    case "chunking":
      return "Preparing…";
    case "paused":
      return "Paused — too many problems (see why)";
    case "failed":
      return "Couldn't read this (see why)";
    case "done":
      return "Ready to study";
    case "registered":
      return "Waiting to start…";
    default:
      return status;
  }
}

/** User-facing error text; raw API detail kept for disclosure. */
export function translateError(detail: string): { message: string; technical: string } {
  const rules: Array<{ match: RegExp; message: string }> = [
    {
      match: /invalid JSON from ollama/i,
      message: "The reading helper couldn't understand the model's answer. Try again or switch profile.",
    },
    {
      match: /file exceeds the .* upload limit/i,
      message: "That file is too large to upload.",
    },
    {
      match: /failed to load/i,
      message: "We couldn't open that file or link.",
    },
    {
      match: /Illegal header value b'Bearer '|OpenRouter API key missing/i,
      message:
        "The OpenRouter API key is missing. Copy .env.example to .env, add your key, and restart the worker.",
    },
    {
      match: /worker offline|nothing will process/i,
      message: "The background worker isn't running, so nothing will process yet.",
    },
    {
      match: /unknown project/i,
      message: "That learning project no longer exists.",
    },
  ];
  for (const rule of rules) {
    if (rule.match.test(detail)) {
      return { message: rule.message, technical: detail };
    }
  }
  return { message: "Something went wrong.", technical: detail };
}

export function detectSourceTypeFromUrl(ref: string): string | null {
  const trimmed = ref.trim();
  if (!trimmed) {
    return null;
  }
  if (/youtube\.com|youtu\.be/i.test(trimmed)) {
    return "youtube";
  }
  if (/^https?:\/\//i.test(trimmed)) {
    return "blog";
  }
  return null;
}
