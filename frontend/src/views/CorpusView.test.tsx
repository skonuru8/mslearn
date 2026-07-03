import type { ReactElement } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { CorpusView } from "./CorpusView";

function wrap(ui: ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

describe("CorpusView", () => {
  it("posts add-source payload and shows new row", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => [] })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ profile: "technical" }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ source_id: "s-new" }) })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [
          {
            source_id: "s-new",
            ref: "/tmp/book.pdf",
            role: "spine",
            status: "running",
            total_chunks: 3,
            done_chunks: 0,
            failed_chunks: 0,
            error: null,
            ts: 1,
          },
        ],
      });
    vi.stubGlobal("fetch", fetchMock);

    wrap(<CorpusView />);
    await screen.findByText("Corpus");

    await userEvent.type(screen.getByLabelText(/Source ref/i), "/tmp/book.pdf");
    await userEvent.click(screen.getByRole("button", { name: "Add source" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/corpus/sources",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            ref: "/tmp/book.pdf",
            role: "spine",
            source_type: null,
            local: true,
          }),
        }),
      );
    });
    await screen.findByText("/tmp/book.pdf");
  });

  it("calls pause endpoint", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [
          {
            source_id: "s1",
            ref: "a.pdf",
            role: "spine",
            status: "running",
            total_chunks: 1,
            done_chunks: 0,
            failed_chunks: 0,
            error: null,
            ts: 1,
          },
        ],
      })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ profile: "technical" }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ source_id: "s1", status: "paused" }) })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [
          {
            source_id: "s1",
            ref: "a.pdf",
            role: "spine",
            status: "paused",
            total_chunks: 1,
            done_chunks: 0,
            failed_chunks: 0,
            error: null,
            ts: 1,
          },
        ],
      })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ profile: "technical" }) });
    vi.stubGlobal("fetch", fetchMock);

    wrap(<CorpusView />);
    await screen.findByText("a.pdf");
    await userEvent.click(screen.getAllByRole("button", { name: "Pause" })[0]!);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/corpus/sources/s1/pause",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("expands failure reasons and retries failed chunks", async () => {
    const row = {
      source_id: "s1",
      ref: "a.pdf",
      role: "spine",
      status: "running",
      total_chunks: 4,
      done_chunks: 2,
      failed_chunks: 2,
      error: null,
      ts: 1,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => [row] })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ profile: "technical" }) })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => [
          { error: "invalid JSON from ollama: ''", count: 2, sample_chunk_ids: ["s1:0", "s1:1"] },
        ],
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ source_id: "s1", status: "running", retried_chunks: 2 }),
      })
      .mockResolvedValueOnce({ ok: true, json: async () => [{ ...row, failed_chunks: 0 }] })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ profile: "technical" }) });
    vi.stubGlobal("fetch", fetchMock);

    wrap(<CorpusView />);
    await screen.findByText("a.pdf");
    await userEvent.click(screen.getByRole("button", { name: /2 failed — why\?/ }));
    await screen.findByText(/invalid JSON from ollama/);

    await userEvent.click(screen.getByRole("button", { name: "Retry failed" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/corpus/sources/s1/retry-failed",
        expect.objectContaining({ method: "POST" }),
      );
    });
  });

  it("shows error banner on 422 ingest", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => [] })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ profile: "technical" }) })
      .mockResolvedValueOnce({
        ok: false,
        status: 422,
        statusText: "Unprocessable Entity",
        json: async () => ({ detail: "failed to load '/bad'" }),
      });
    vi.stubGlobal("fetch", fetchMock);

    wrap(<CorpusView />);
    await screen.findByText("Corpus");
    await userEvent.type(screen.getByLabelText(/Source ref/i), "/bad");
    await userEvent.click(screen.getByRole("button", { name: "Add source" }));
    await screen.findByText("failed to load '/bad'");
  });
});
