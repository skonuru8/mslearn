import type { ChatFrame } from "./types";
import { projectHeaders } from "./projectId";

export class ApiError extends Error {
  status: number;

  constructor(detail: string, status: number) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  for (const [key, value] of Object.entries(projectHeaders())) {
    headers.set(key, value);
  }
  if (init?.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string | unknown };
      if (typeof body.detail === "string") {
        detail = body.detail;
      } else if (body.detail !== undefined) {
        detail = JSON.stringify(body.detail);
      }
    } catch {
      // keep statusText
    }
    throw new ApiError(detail, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

/** Parse complete SSE event blocks from a buffer; returns unconsumed tail. */
export function parseSseBuffer(buffer: string): { frames: ChatFrame[]; rest: string } {
  const frames: ChatFrame[] = [];
  const parts = buffer.split("\n\n");
  const rest = parts.pop() ?? "";

  for (const part of parts) {
    const line = part
      .split("\n")
      .map((row) => row.trim())
      .find((row) => row.startsWith("data: "));
    if (!line) {
      continue;
    }
    const payload = line.slice("data: ".length);
    try {
      frames.push(JSON.parse(payload) as ChatFrame);
    } catch {
      // malformed frame (e.g. truncated at connection drop) — skip, keep stream alive
    }
  }

  return { frames, rest };
}

export async function streamChat(
  question: string,
  sessionId: string,
  onDelta: (delta: string) => void,
  onDone: (citations: string[]) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...projectHeaders() },
    body: JSON.stringify({ question, session_id: sessionId }),
    signal,
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) {
        detail = body.detail;
      }
    } catch {
      // keep statusText
    }
    throw new ApiError(detail, response.status);
  }

  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error("streaming response has no body");
  }

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const parsed = parseSseBuffer(buffer);
    buffer = parsed.rest;
    for (const frame of parsed.frames) {
      handleFrame(frame, onDelta, onDone);
    }
  }

  if (buffer.trim()) {
    const parsed = parseSseBuffer(`${buffer}\n\n`);
    for (const frame of parsed.frames) {
      handleFrame(frame, onDelta, onDone);
    }
  }
}

function handleFrame(
  frame: ChatFrame,
  onDelta: (delta: string) => void,
  onDone: (citations: string[]) => void,
): void {
  if ("error" in frame) {
    throw new ApiError(frame.error, 502);
  }
  if ("delta" in frame) {
    onDelta(frame.delta);
  } else if ("done" in frame && frame.done) {
    onDone(frame.citations);
  }
}

/**
 * Uploads via XMLHttpRequest (not fetch) so the caller can render a real
 * file-transfer progress bar via `upload.onprogress` — fetch has no
 * upload-progress event.
 */
export function uploadSource(
  file: File,
  role: string,
  local: boolean,
  onProgress?: (percent: number) => void,
): Promise<{ source_id: string; stored_path: string }> {
  const form = new FormData();
  form.append("file", file);
  form.append("role", role);
  form.append("local", String(local));

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/corpus/upload");
    for (const [key, value] of Object.entries(projectHeaders())) {
      xhr.setRequestHeader(key, value);
    }
    xhr.upload.onprogress = (event) => {
      if (onProgress && event.lengthComputable) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };
    xhr.onload = () => {
      let body: unknown;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        body = undefined;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as { source_id: string; stored_path: string });
        return;
      }
      const detail =
        body && typeof body === "object" && "detail" in body && typeof (body as { detail?: unknown }).detail === "string"
          ? (body as { detail: string }).detail
          : xhr.statusText || `upload failed (${xhr.status})`;
      reject(new ApiError(detail, xhr.status));
    };
    xhr.onerror = () => reject(new ApiError("network error during upload", 0));
    xhr.send(form);
  });
}
