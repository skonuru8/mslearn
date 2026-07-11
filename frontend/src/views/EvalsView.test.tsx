import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { EvalsView } from "./EvalsView";
import { installFetchMock } from "../test/fetchMock";

function reportHandlers(overrides: Record<string, () => unknown> = {}) {
  return {
    "/api/evals/report": () => ({ run: null, metrics: [] }),
    "/api/evals/pending": () => [],
    ...overrides,
  };
}

describe("EvalsView", () => {
  it("renders per-metric values, gates, and pass/fail for a populated report", async () => {
    installFetchMock(
      reportHandlers({
        "/api/evals/report": () => ({
          run: { id: 3, ts: 1_700_000_000, kind: "full", git_sha: "abc1234", passed: 0 },
          metrics: [
            { metric: "extraction.precision", value: 0.92, gate: 0.9, passed: 1 },
            { metric: "clustering.f1", value: 0.5, gate: 0.8, passed: 0 },
          ],
        }),
      }),
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
    installFetchMock(reportHandlers());

    render(<EvalsView />);

    await screen.findByText(/No eval run yet/i);
    expect(screen.getByText(/python -m mslearn.evals.run/i)).toBeInTheDocument();
  });

  it("renders pending prompt proposals with metrics, why, and diff", async () => {
    installFetchMock(
      reportHandlers({
        "/api/evals/pending": () => [
          {
            run_id: 7,
            ts: 1_700_000_000,
            proposal: {
              kind: "prompt",
              key: "prompt:rubric_teach",
              new_prompt: "Score {concept_name} using {markdown} more strictly.",
              targets_metric: "extraction.recall",
              why: "clarify rubric wording",
            },
            shadow_before: { "extraction.recall": 0.85 },
            shadow_after: { "extraction.recall": 0.9 },
            why: "clarify rubric wording",
          },
        ],
      }),
    );

    render(<EvalsView />);

    await screen.findByText(/pending prompt changes/i);
    expect(screen.getByText(/extraction.recall/i)).toBeInTheDocument();
    expect(screen.getByText(/clarify rubric wording/i)).toBeInTheDocument();
    expect(screen.getByText(/0.85/)).toBeInTheDocument();
    expect(screen.getByText(/0.9/)).toBeInTheDocument();
    expect(screen.getByText(/more strictly/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /approve/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /reject/i })).toBeInTheDocument();
  });

  it("approves a pending run via POST and removes it from the list", async () => {
    const fetchMock = installFetchMock(
      reportHandlers({
        "/api/evals/pending": () => [
          {
            run_id: 7,
            ts: 1_700_000_000,
            proposal: {
              kind: "prompt",
              key: "prompt:rubric_teach",
              new_prompt: "New rubric text",
              targets_metric: "extraction.recall",
              why: "clarify rubric wording",
            },
            shadow_before: { "extraction.recall": 0.85 },
            shadow_after: { "extraction.recall": 0.9 },
            why: "clarify rubric wording",
          },
        ],
        "/api/evals/pending/7/approve": () => ({ run_id: 7, status: "applied" }),
      }),
    );

    render(<EvalsView />);
    await screen.findByText(/pending prompt changes/i);
    await userEvent.click(screen.getByRole("button", { name: /approve/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/evals/pending/7/approve",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("rejects a pending run via POST", async () => {
    const fetchMock = installFetchMock(
      reportHandlers({
        "/api/evals/pending": () => [
          {
            run_id: 9,
            ts: 1_700_000_000,
            proposal: {
              kind: "prompt",
              key: "prompt:rubric_teach",
              new_prompt: "New rubric text",
              targets_metric: "extraction.recall",
              why: "clarify rubric wording",
            },
            shadow_before: { "extraction.recall": 0.85 },
            shadow_after: { "extraction.recall": 0.9 },
            why: "clarify rubric wording",
          },
        ],
        "/api/evals/pending/9/reject": () => ({ run_id: 9, status: "rejected" }),
      }),
    );

    render(<EvalsView />);
    await screen.findByText(/pending prompt changes/i);
    await userEvent.click(screen.getByRole("button", { name: /reject/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/evals/pending/9/reject",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });
});
