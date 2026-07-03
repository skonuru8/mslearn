import { describe, expect, it, vi } from "vitest";
import { ApiError, api, parseSseBuffer } from "./client";

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
