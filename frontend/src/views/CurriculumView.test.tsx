import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";
import { CurriculumView } from "./CurriculumView";
import { ProjectProvider } from "../context/ProjectContext";

function mockFetchForConcepts(concepts: unknown[], outline?: unknown) {
  const fetchMock = vi.fn(async (url: string) => {
    const path = url.startsWith("http") ? new URL(url).pathname : url;
    if (path === "/api/projects") {
      return {
        ok: true,
        json: async () => [{ project_id: "default", name: "Default", created_ts: 0 }],
      };
    }
    if (path === "/api/study/curriculum") {
      return { ok: true, json: async () => concepts };
    }
    if (path === "/api/study/outline") {
      return { ok: true, json: async () => outline ?? {} };
    }
    if (path === "/api/corpus/synthesis/status") {
      return { ok: true, json: async () => ({ last_run: null }) };
    }
    return { ok: true, json: async () => ({}) };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderCurriculum() {
  return render(
    <MemoryRouter initialEntries={["/curriculum"]}>
      <ProjectProvider>
        <Routes>
          <Route path="/curriculum" element={<CurriculumView />} />
        </Routes>
      </ProjectProvider>
    </MemoryRouter>,
  );
}

describe("CurriculumView", () => {
  it("groups concepts by category, with uncategorized concepts under 'Other'", async () => {
    mockFetchForConcepts([
      { concept_id: "k1", name: "Loops", summary: "Iterate.", order_index: 0, category: "Numbers" },
      { concept_id: "k2", name: "Arrays", summary: "Store lists.", order_index: 1, category: "Numbers" },
      { concept_id: "k3", name: "Closures", summary: "Capture scope.", order_index: 2, category: "" },
    ]);

    renderCurriculum();

    expect(await screen.findByText("Numbers")).toBeInTheDocument();
    expect(await screen.findByText("Other")).toBeInTheDocument();
    expect(screen.getByText(/Loops/)).toBeInTheDocument();
    expect(screen.getByText(/Arrays/)).toBeInTheDocument();
    expect(screen.getByText(/Closures/)).toBeInTheDocument();
  });

  it("renders a flat list when no concept has a category", async () => {
    mockFetchForConcepts([
      { concept_id: "k1", name: "Loops", summary: "Iterate.", order_index: 0, category: "" },
      { concept_id: "k2", name: "Arrays", summary: "Store lists.", order_index: 1, category: "" },
    ]);

    renderCurriculum();

    expect(await screen.findByText(/Loops/)).toBeInTheDocument();
    expect(screen.getByText(/Arrays/)).toBeInTheDocument();
    expect(screen.queryByText("Other")).not.toBeInTheDocument();
  });

  it("renders a chapter/section outline tree when the outline endpoint reports structure", async () => {
    mockFetchForConcepts(
      [
        { concept_id: "k1", name: "Numbers", summary: "About numbers.", order_index: 0, category: "Ch1" },
        { concept_id: "k2", name: "History", summary: "About history.", order_index: 1, category: "Ch2" },
      ],
      {
        has_structure: true,
        tree: [
          {
            title: "Ch1",
            concepts: [],
            children: [
              {
                title: "1.1 Basics",
                concepts: [{ concept_id: "k1", name: "Numbers", conflict_count: 0 }],
                children: [],
              },
            ],
          },
          {
            title: "Ch2",
            concepts: [{ concept_id: "k2", name: "History", conflict_count: 0 }],
            children: [],
          },
        ],
        flat: [],
      },
    );

    renderCurriculum();

    expect(await screen.findByText("Ch1")).toBeInTheDocument();
    expect(screen.getByText("1.1 Basics")).toBeInTheDocument();
    expect(screen.getByText("Ch2")).toBeInTheDocument();
    expect(screen.getByText(/Numbers/)).toBeInTheDocument();
    expect(screen.getByText(/History/)).toBeInTheDocument();
  });

  it("renders the flat category list when the outline endpoint reports no structure (Spec-A regression)", async () => {
    mockFetchForConcepts(
      [
        { concept_id: "k1", name: "Loops", summary: "Iterate.", order_index: 0, category: "Numbers" },
        { concept_id: "k2", name: "Arrays", summary: "Store lists.", order_index: 1, category: "Numbers" },
      ],
      {
        has_structure: false,
        tree: [],
        flat: [
          { concept_id: "k1", name: "Loops", conflict_count: 0 },
          { concept_id: "k2", name: "Arrays", conflict_count: 0 },
        ],
      },
    );

    renderCurriculum();

    expect(await screen.findByText("Numbers")).toBeInTheDocument();
    expect(screen.getByText(/Loops/)).toBeInTheDocument();
    expect(screen.getByText(/Arrays/)).toBeInTheDocument();
    expect(screen.queryByText("outline-node")).not.toBeInTheDocument();
  });

  it("polls the curriculum every 15s while building, and pauses when the tab is hidden", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let visibility: DocumentVisibilityState = "visible";
    const visibilitySpy = vi
      .spyOn(document, "visibilityState", "get")
      .mockImplementation(() => visibility);

    let curriculumCalls = 0;
    const fetchMock = vi.fn(async (url: string) => {
      const path = url.startsWith("http") ? new URL(url).pathname : url;
      if (path === "/api/projects") {
        return {
          ok: true,
          json: async () => [{ project_id: "default", name: "Default", created_ts: 0 }],
        };
      }
      if (path === "/api/study/curriculum") {
        curriculumCalls += 1;
        return { ok: true, json: async () => [] };
      }
      if (path === "/api/corpus/synthesis/status") {
        return { ok: true, json: async () => ({ running_since: 1, last_run: null, progress: null }) };
      }
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);

    try {
      renderCurriculum();
      await vi.waitFor(() => expect(screen.queryByText(/Loading/i)).toBeNull());
      expect(curriculumCalls).toBe(1);

      await vi.advanceTimersByTimeAsync(14_000);
      expect(curriculumCalls).toBe(1);

      await vi.advanceTimersByTimeAsync(1_000);
      expect(curriculumCalls).toBe(2);

      visibility = "hidden";
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(15_000);
      expect(curriculumCalls).toBe(2);
    } finally {
      visibilitySpy.mockRestore();
      vi.useRealTimers();
    }
  });
});
