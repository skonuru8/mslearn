import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EvalsView } from "./EvalsView";

describe("EvalsView", () => {
  it("renders per-metric values, gates, and pass/fail for a populated report", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({
          run: { id: 3, ts: 1_700_000_000, kind: "full", git_sha: "abc1234", passed: 0 },
          metrics: [
            { metric: "extraction.precision", value: 0.92, gate: 0.9, passed: 1 },
            { metric: "clustering.f1", value: 0.5, gate: 0.8, passed: 0 },
          ],
        }),
      })),
    );

    render(<EvalsView />);

    await screen.findByText("Extraction precision");
    expect(screen.getByText("Clustering F1")).toBeInTheDocument();
    expect(screen.getByText("0.92")).toBeInTheDocument();
    expect(screen.getByText("0.80")).toBeInTheDocument();
    expect(screen.getByText("Pass")).toBeInTheDocument();
    expect(screen.getByText("Fail")).toBeInTheDocument();
    expect(screen.getByText(/some gates failed/i)).toBeInTheDocument();
  });

  it("renders a clean empty state when no eval run exists", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ run: null, metrics: [] }),
      })),
    );

    render(<EvalsView />);

    await screen.findByText(/No eval run yet/i);
    expect(screen.getByText(/python -m mslearn.evals.run/i)).toBeInTheDocument();
  });
});
