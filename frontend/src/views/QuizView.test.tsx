import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { QuizView } from "./QuizView";

describe("QuizView", () => {
  it("submits answer and renders grade", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ concept_id: "k1", question: "Why TTL?" }),
      })
      .mockResolvedValueOnce({ ok: true, json: async () => [] })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          correct: true,
          score_0_100: 90,
          explanation: "Good reasoning [claim:c1]",
        }),
      })
      .mockResolvedValueOnce({ ok: true, json: async () => [{ concept_id: "k1", attempts: 1, avg_score: 90, last_correct: true }] });
    vi.stubGlobal("fetch", fetchMock);

    render(<QuizView />);
    await screen.findByText("Why TTL?");
    await userEvent.type(screen.getByLabelText(/Your answer/i), "Because stale data");
    await userEvent.click(screen.getByRole("button", { name: "Submit" }));

    await screen.findByText(/Score 90/);
    await userEvent.click(screen.getByRole("button", { name: "Next question" }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/quiz/next", expect.anything());
    });
  });
});
