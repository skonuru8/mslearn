import { describe, expect, it } from "vitest";
import { detectSourceTypeFromUrl, sourceStatusLabel, translateError } from "./userMessages";

describe("userMessages", () => {
  it("maps pipeline statuses to plain language", () => {
    expect(sourceStatusLabel("running")).toBe("Reading…");
    expect(sourceStatusLabel("done")).toBe("Ready to study");
  });

  it("detects youtube and blog links", () => {
    expect(detectSourceTypeFromUrl("https://www.youtube.com/watch?v=abc")).toBe("youtube");
    expect(detectSourceTypeFromUrl("https://example.com/post")).toBe("blog");
    expect(detectSourceTypeFromUrl("/local/path.pdf")).toBeNull();
  });

  it("translates common API errors", () => {
    const result = translateError("invalid JSON from ollama: ''");
    expect(result.message).toMatch(/reading helper/i);
    expect(result.technical).toContain("ollama");
    expect(translateError("failed to load '/bad'").message).toMatch(/couldn't open that file or link/i);
  });

  it("translates missing OpenRouter key errors", () => {
    const result = translateError("OpenRouter API key missing — set MSL_OPENROUTER_API_KEY in .env");
    expect(result.message).toMatch(/OpenRouter API key is missing/i);
  });
});
