import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AdminBar } from "./AdminBar";

function statusResponse(overrides: Partial<{ worker: boolean }> = {}) {
  return {
    ok: true,
    json: async () => ({
      worker: overrides.worker ?? true,
      redis: true,
      neo4j: true,
      spend: { total_cost_usd: 0, total_calls: 0 },
      synthesis: { last_run: null, last_error: null },
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

    render(<AdminBar />);
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

    render(<AdminBar />);
    await screen.findByText(/Worker offline/);
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
      render(<AdminBar />);
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
