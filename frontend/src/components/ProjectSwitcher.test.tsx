import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";
import { ProjectSwitcher } from "./ProjectSwitcher";
import { ProjectProvider } from "../context/ProjectContext";

describe("ProjectSwitcher", () => {
  it("navigates to the curriculum when the project changes", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      const path = url.startsWith("http") ? new URL(url).pathname : url;
      if (path === "/api/projects") {
        return {
          ok: true,
          json: async () => [
            { project_id: "default", name: "Default", created_ts: 0 },
            { project_id: "p2", name: "Biology", created_ts: 0 },
          ],
        };
      }
      return { ok: true, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/concepts/abc123"]}>
        <ProjectProvider>
          <ProjectSwitcher />
          <Routes>
            <Route path="/concepts/:id" element={<div>concept page</div>} />
            <Route path="/curriculum" element={<div>my course list</div>} />
          </Routes>
        </ProjectProvider>
      </MemoryRouter>,
    );

    await screen.findByRole("combobox", { name: /learning project/i });
    fireEvent.change(screen.getByRole("combobox", { name: /learning project/i }), {
      target: { value: "p2" },
    });
    expect(await screen.findByText("my course list")).toBeInTheDocument();
  });
});
