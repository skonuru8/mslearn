import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryView } from "./MemoryView";

describe("MemoryView", () => {
  it("deletes a memory row", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          items: [{ memory_id: "m1", text: "likes examples", category: "preference", created_at: 1 }],
        }),
      })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ deleted: true }) });
    vi.stubGlobal("fetch", fetchMock);

    render(<MemoryView />);
    await screen.findByText("likes examples");
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => {
      expect(screen.queryByText("likes examples")).not.toBeInTheDocument();
    });
  });

  it("renders plain-language unavailable state on 503 with a technical details disclosure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 503,
        statusText: "Service Unavailable",
        json: async () => ({ detail: "learner memory unavailable: neo4j connection refused" }),
      })),
    );

    render(<MemoryView />);
    await screen.findByText(/Personal memory is off/i);
    expect(screen.queryByText(/won't personalize/i)).toBeInTheDocument();

    const details = screen.getByText("Show technical details");
    await userEvent.click(details);
    await screen.findByText(/neo4j connection refused/i);
  });
});
