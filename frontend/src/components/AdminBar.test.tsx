import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AdminBar } from "./AdminBar";

describe("AdminBar", () => {
  it("switches profile via POST and re-renders active", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ active: "openrouter", available: ["openrouter", "offline"] }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          recent_calls: [],
          total_cost_usd: 0,
          total_calls: 0,
          by_role: {},
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ api: true, worker: true, redis: true, neo4j: true }),
      })
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

  it("shows a red worker-offline chip when the health check reports no worker", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ active: "openrouter", available: ["openrouter"] }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ recent_calls: [], total_cost_usd: 0, total_calls: 0, by_role: {} }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ api: true, worker: false, redis: true, neo4j: true }),
      });
    vi.stubGlobal("fetch", fetchMock);

    render(<AdminBar />);
    await screen.findByText(/Worker offline/);
  });
});
