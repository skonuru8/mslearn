import { describe, expect, it, vi } from "vitest";
import { ApiError, api, parseSseBuffer, uploadSource } from "./client";

function makeFakeXHRClass(status: number, response: unknown, statusText = "OK") {
  return class FakeXHR {
    upload: {
      onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null;
    } = { onprogress: null };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    status = status;
    statusText = statusText;
    responseText = "";

    open(_method: string, _url: string): void {}

    send(_body: FormData): void {
      this.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 });
      this.upload.onprogress?.({ lengthComputable: true, loaded: 100, total: 100 });
      this.responseText = JSON.stringify(response);
      this.onload?.();
    }
  };
}

describe("parseSseBuffer", () => {
  it("parses delta and done frames", () => {
    const input = 'data: {"delta":"Hello"}\n\ndata: {"done":true,"citations":["c1"]}\n\n';
    const { frames, rest } = parseSseBuffer(input);
    expect(frames).toEqual([
      { delta: "Hello" },
      { done: true, citations: ["c1"] },
    ]);
    expect(rest).toBe("");
  });

  it("buffers partial chunks until event delimiter", () => {
    const part1 = 'data: {"delta":"Hel';
    const { frames: f1, rest: r1 } = parseSseBuffer(part1);
    expect(f1).toEqual([]);
    expect(r1).toBe(part1);

    const combined = r1 + 'lo"}\n\n';
    const { frames: f2, rest: r2 } = parseSseBuffer(combined);
    expect(f2).toEqual([{ delta: "Hello" }]);
    expect(r2).toBe("");
  });
});

describe("api", () => {
  it("throws ApiError with backend detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 422,
        statusText: "Unprocessable Entity",
        json: async () => ({ detail: "bad ref" }),
      })),
    );

    await expect(api("/api/corpus/sources")).rejects.toEqual(
      expect.objectContaining({ message: "bad ref", status: 422 }),
    );
    expect(await api("/api/corpus/sources").catch((e) => e)).toBeInstanceOf(ApiError);
  });
});

describe("uploadSource", () => {
  it("reports transfer progress via XHR and resolves with the response body", async () => {
    vi.stubGlobal(
      "XMLHttpRequest",
      makeFakeXHRClass(200, { source_id: "s1", stored_path: "/tmp/a.pdf" }) as unknown as typeof XMLHttpRequest,
    );
    const progress: number[] = [];
    const result = await uploadSource(
      new File(["x"], "a.pdf"),
      "spine",
      false,
      (percent) => progress.push(percent),
    );
    expect(result).toEqual({ source_id: "s1", stored_path: "/tmp/a.pdf" });
    expect(progress).toEqual([50, 100]);
  });

  it("rejects with the backend detail on a non-2xx status", async () => {
    vi.stubGlobal(
      "XMLHttpRequest",
      makeFakeXHRClass(
        413,
        { detail: "file exceeds the 500 MB upload limit" },
        "Payload Too Large",
      ) as unknown as typeof XMLHttpRequest,
    );
    await expect(uploadSource(new File(["x"], "big.pdf"), "spine", false)).rejects.toEqual(
      expect.objectContaining({ message: "file exceeds the 500 MB upload limit", status: 413 }),
    );
  });
});

describe("error frames and malformed payloads", () => {
  it("skips malformed frames instead of throwing", () => {
    const input = 'data: {not json}\n\ndata: {"delta":"ok"}\n\n';
    const { frames } = parseSseBuffer(input);
    expect(frames).toEqual([{ delta: "ok" }]);
  });

  it("parses error frames", () => {
    const { frames } = parseSseBuffer('data: {"error":"backend fell over"}\n\n');
    expect(frames).toEqual([{ error: "backend fell over" }]);
  });
});
