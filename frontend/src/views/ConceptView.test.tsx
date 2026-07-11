import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { ConceptView } from "./ConceptView";
import { installFetchMock } from "../test/fetchMock";

function conceptDetailBody() {
  return {
    concept: { concept_id: "k1", name: "Cache", summary: "Stale data" },
    claims: [{ claim_id: "c1", text: "Hard problem", stance: "neutral", source_id: "s1" }],
    conflicts: [],
    citations: [{ claim_id: "c1", quote: "cache invalidation is hard", page: 3 }],
  };
}

function teachBody(overrides: Record<string, unknown> = {}) {
  return {
    guide: {
      concept_id: "k1",
      title: "Cache",
      tl_dr: { text: "Caches speed up reads.", claims: ["c1"] },
      skeleton: ["Basics"],
      sections: [
        {
          id: "s1",
          title: "Basics",
          items: [{ kind: "claim", text: "Hard problem", claims: ["c1"] }],
        },
      ],
      disagreements: [],
      open_questions: [],
    },
    cached: false,
    progress: {},
    ...overrides,
  };
}

function baseHandlers(overrides: Record<string, (path: string, init?: RequestInit) => unknown> = {}) {
  return {
    "/api/study/concepts/k1/teach": () => teachBody(),
    "/api/study/concepts/k1/feedback": () => ({}),
    "/api/study/concepts/k1": () => conceptDetailBody(),
    ...overrides,
  };
}

function renderConcept(path = "/concepts/k1") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/concepts/:id" element={<ConceptView />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ConceptView", () => {
  it("flags claim and refetches", async () => {
    vi.stubGlobal(
      "prompt",
      vi.fn(() => "bad quote"),
    );

    let flagged = false;
    const fetchMock = installFetchMock(
      baseHandlers({
        "/api/study/concepts/k1": () =>
          flagged ? { ...conceptDetailBody(), claims: [], citations: [] } : conceptDetailBody(),
        "/api/study/claims/c1/flag": () => {
          flagged = true;
          return { claim_id: "c1", concept_id: "k1", status: "flagged" };
        },
      }),
    );

    renderConcept();

    await screen.findByText("Cache");
    await userEvent.click(screen.getByRole("button", { name: "Flag" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/study/claims/c1/flag",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("shows a neutral 'not in this project' panel on 404, not an error", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      statusText: "Not Found",
      json: async () => ({ detail: "unknown concept 'x'" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    renderConcept("/concepts/x");

    expect(await screen.findByText(/not part of this project/i)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders the interactive study guide instead of markdown", async () => {
    installFetchMock(baseHandlers());

    renderConcept();

    expect(await screen.findByRole("heading", { name: "Basics" })).toBeInTheDocument();
    expect(screen.getByText(/Caches speed up reads\./)).toBeInTheDocument();
  });

  it("does not double-render the concept summary in the header", async () => {
    // The concept's one-liner summary must appear only through the guide's
    // lede (tl_dr), not again as a standalone <p> under the <h1>.
    installFetchMock(
      baseHandlers({
        "/api/study/concepts/k1/teach": () =>
          teachBody({
            guide: {
              ...teachBody().guide,
              tl_dr: { text: "Stale data", claims: ["c1"] },
            },
          }),
      }),
    );

    renderConcept();

    await screen.findByRole("heading", { name: "Cache" });
    expect(screen.getAllByText(/Stale data/)).toHaveLength(1);
  });

  it("toggles section reviewed and posts to /progress", async () => {
    const fetchMock = installFetchMock(
      baseHandlers({
        "/api/study/concepts/k1/progress": () => ({ progress: { s1: true } }),
      }),
    );

    renderConcept();

    await screen.findByRole("heading", { name: "Basics" });
    await userEvent.click(screen.getByRole("checkbox", { name: /reviewed/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/study/concepts/k1/progress",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ section_id: "s1", reviewed: true }),
        }),
      );
    });
  });

  it("makes on-demand flashcards with the chosen count and shows a flip card", async () => {
    const fetchMock = installFetchMock(
      baseHandlers({
        "/api/study/concepts/k1/flashcards": () => ({
          cards: [{ front: "What speeds up reads?", back: "A cache.", claims: ["c1"] }],
        }),
      }),
    );

    renderConcept();

    await screen.findByRole("heading", { name: "Basics" });
    const countInput = screen.getByLabelText(/count/i);
    await userEvent.clear(countInput);
    await userEvent.type(countInput, "3");
    await userEvent.click(screen.getByRole("button", { name: /make flashcards/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/study/concepts/k1/flashcards",
        expect.objectContaining({ method: "POST", body: JSON.stringify({ count: 3 }) }),
      );
    });

    const card = await screen.findByText("What speeds up reads?");
    const flashcard = card.closest(".flashcard");
    expect(flashcard).not.toBeNull();
    expect(flashcard).not.toHaveClass("is-flipped");
    await userEvent.click(card);
    expect(flashcard).toHaveClass("is-flipped");
  });

  it("omits the self-check panel gracefully when the backend returns none", async () => {
    const fetchMock = installFetchMock(
      baseHandlers({
        "/api/study/concepts/k1/selfcheck": () => ({ checks: [] }),
      }),
    );

    renderConcept();

    await screen.findByRole("heading", { name: "Basics" });
    await userEvent.click(screen.getByRole("button", { name: /self-check/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/study/concepts/k1/selfcheck",
        expect.objectContaining({ method: "POST" }),
      );
    });
    expect(await screen.findByText(/no grounded self-check/i)).toBeInTheDocument();
  });
});
