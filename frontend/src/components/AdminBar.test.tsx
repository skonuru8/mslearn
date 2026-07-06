import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AdminBar } from "./AdminBar";

function renderAdminBar() {
  return render(
    <MemoryRouter>
      <AdminBar />
    </MemoryRouter>,
  );
}

function statusResponse(
  overrides: Partial<{ worker: boolean; dead_letter_count: number }> = {},
) {
  return {
    ok: true,
    json: async () => ({
      worker: overrides.worker ?? true,
      redis: true,
      neo4j: true,
      spend: { total_cost_usd: 0, total_calls: 0 },
      synthesis: { last_run: null, last_error: null },
      ...(overrides.dead_letter_count !== undefined
        ? { dead_letter_count: overrides.dead_letter_count }
        : {}),
    }),
  };
}

describe("AdminBar", () => {
  it("switches profile via POST and re-renders active", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ active: "openrouter", available: ["openrouter", "offline"] }),
      })
      .mockResolvedValueOnce(statusResponse())
      .mockResolvedValueOnce({ ok: true, json: async () => ({ active: "offline" }) })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ active: "offline", available: ["openrouter", "offline"] }),
      });
    vi.stubGlobal("fetch", fetchMock);

    renderAdminBar();
    await screen.findByDisplayValue("openrouter");
    await userEvent.selectOptions(screen.getByRole("combobox"), "offline");

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/admin/profiles/offline",
        expect.objectContaining({ method: "POST" }),
      );
    });
    await screen.findByDisplayValue("offline");
  });

  it("shows a red worker-offline chip when the status poll reports no worker", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ active: "openrouter", available: ["openrouter"] }),
      })
      .mockResolvedValueOnce(statusResponse({ worker: false }));
    vi.stubGlobal("fetch", fetchMock);

    renderAdminBar();
    await screen.findByText(/Worker offline/);
  });

  it("warns about stuck background jobs when the status poll reports dead letters", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ active: "openrouter", available: ["openrouter"] }),
      })
      .mockResolvedValueOnce(statusResponse({ dead_letter_count: 2 }));
    vi.stubGlobal("fetch", fetchMock);

    renderAdminBar();
    await screen.findByText(/2 background jobs are stuck/);
  });

  it("shows no stuck-jobs warning when the status has no dead letters", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ active: "openrouter", available: ["openrouter"] }),
      })
      .mockResolvedValueOnce(statusResponse());
    vi.stubGlobal("fetch", fetchMock);

    renderAdminBar();
    await screen.findByText("Background worker running");
    expect(screen.queryByText(/stuck/)).toBeNull();
  });

  it("does not poll /api/status while the tab is hidden, but resumes on visibility", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let visibility: DocumentVisibilityState = "visible";
    const visibilitySpy = vi
      .spyOn(document, "visibilityState", "get")
      .mockImplementation(() => visibility);

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ active: "openrouter", available: ["openrouter"] }),
      })
      .mockResolvedValue(statusResponse());
    vi.stubGlobal("fetch", fetchMock);

    try {
      renderAdminBar();
      await vi.waitFor(() => {
        expect(fetchMock).toHaveBeenCalledWith("/api/status", expect.anything());
      });
      const callsWhileVisible = fetchMock.mock.calls.length;

      visibility = "hidden";
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(120_000);
      expect(fetchMock.mock.calls.length).toBe(callsWhileVisible);

      visibility = "visible";
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.waitFor(() => {
        expect(fetchMock.mock.calls.length).toBeGreaterThan(callsWhileVisible);
      });
    } finally {
      visibilitySpy.mockRestore();
      vi.useRealTimers();
    }
  });
});
