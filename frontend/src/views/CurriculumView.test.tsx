import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";
import { CurriculumView } from "./CurriculumView";
import { ProjectProvider } from "../context/ProjectContext";

function mockFetchForConcepts(concepts: unknown[]) {
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
});
