import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { countClaimCitations } from "../components/citations";
import { ConceptView } from "./ConceptView";

describe("ConceptView", () => {
  it("flags claim and refetches", async () => {
    vi.stubGlobal(
      "prompt",
      vi.fn(() => "bad quote"),
    );

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          concept: { concept_id: "k1", name: "Cache", summary: "Stale data" },
          claims: [{ claim_id: "c1", text: "Hard problem", stance: "neutral", source_id: "s1" }],
          conflicts: [],
          citations: [{ claim_id: "c1", source_id: "s1" }],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ markdown: "## Explanation\n\nHard [claim:c1]" }),
      })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ claim_id: "c1", status: "flagged" }) })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          concept: { concept_id: "k1", name: "Cache", summary: "Stale data" },
          claims: [],
          conflicts: [],
          citations: [],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ markdown: "## Explanation\n\nRegenerated" }),
      });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/concepts/k1"]}>
        <Routes>
          <Route path="/concepts/:id" element={<ConceptView />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("Cache");
    expect(countClaimCitations("Hard [claim:c1]")).toBe(1);
    await userEvent.click(screen.getByRole("button", { name: "Flag" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/study/claims/c1/flag",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });
});
