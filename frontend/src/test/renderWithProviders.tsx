import type { ReactElement } from "react";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ProjectProvider } from "../context/ProjectContext";

export function renderWithProviders(ui: ReactElement) {
  return render(
    <MemoryRouter>
      <ProjectProvider>{ui}</ProjectProvider>
    </MemoryRouter>,
  );
}

export function mockProjectsFetch(fetchMock: ReturnType<typeof import("vitest").vi.fn>) {
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => [{ project_id: "default", name: "Default", created_ts: 1 }],
  });
}
