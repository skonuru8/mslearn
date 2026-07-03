import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { CorpusView } from "./CorpusView";
import { corpusHandlers, installFetchMock } from "../test/fetchMock";
import { renderWithProviders } from "../test/renderWithProviders";

describe("CorpusView", () => {
  it("posts add-link payload and shows new row", async () => {
    installFetchMock(
      corpusHandlers([], {
        postSource: () => ({ source_id: "s-new" }),
        "/api/corpus/sources": (_path, init) => {
          if (init?.method === "POST") {
            return { source_id: "s-new" };
          }
          return [
            {
              source_id: "s-new",
              ref: "/tmp/book.pdf",
              role: "spine",
              status: "registered",
              total_chunks: 3,
              done_chunks: 0,
              failed_chunks: 0,
              rejected_chunks: 0,
              error: null,
              ts: 1,
            },
          ];
        },
      }),
    );

    renderWithProviders(<CorpusView />);
    await screen.findByText("My materials");

    await userEvent.click(screen.getByRole("tab", { name: "From a link" }));
    await userEvent.type(
      screen.getByLabelText(/Paste a YouTube or article link/i),
      "/tmp/book.pdf",
    );
    await userEvent.click(screen.getByRole("button", { name: "Add link" }));

    await screen.findByText("/tmp/book.pdf");
  });

  it("renders ingestion progress for a running source", async () => {
    installFetchMock(
      corpusHandlers([
        {
          source_id: "s1",
          ref: "a.pdf",
          role: "spine",
          status: "running",
          total_chunks: 10,
          done_chunks: 3,
          failed_chunks: 1,
          rejected_chunks: 0,
          error: null,
          ts: 1,
        },
      ]),
    );

    renderWithProviders(<CorpusView />);
    await screen.findByText(/Reading… 4 of 10 sections · 1 problems/);
    expect(screen.getByRole("progressbar")).toBeTruthy();
  });

  it("calls pause endpoint", async () => {
    const row = {
      source_id: "s1",
      ref: "a.pdf",
      role: "spine",
      status: "running",
      total_chunks: 1,
      done_chunks: 0,
      failed_chunks: 0,
      rejected_chunks: 0,
      error: null,
      ts: 1,
    };
    let paused = false;
    installFetchMock(
      corpusHandlers([row], {
        "/api/corpus/sources/s1/pause": () => {
          paused = true;
          return { source_id: "s1", status: "paused" };
        },
        "/api/corpus/sources": (path, init) => {
          if (path.endsWith("/pause") && init?.method === "POST") {
            paused = true;
            return { source_id: "s1", status: "paused" };
          }
          return [{ ...row, status: paused ? "paused" : row.status }];
        },
      }),
    );

    renderWithProviders(<CorpusView />);
    await screen.findByText("a.pdf");
    await userEvent.click(screen.getAllByRole("button", { name: "Pause" })[0]!);
    await waitFor(() => expect(paused).toBe(true));
  });

  it("expands failure reasons and retries failed chunks", async () => {
    const row = {
      source_id: "s1",
      ref: "a.pdf",
      role: "spine",
      status: "paused",
      total_chunks: 4,
      done_chunks: 2,
      failed_chunks: 2,
      rejected_chunks: 0,
      error: null,
      ts: 1,
    };
    let retried = false;
    installFetchMock({
      ...corpusHandlers([row]),
      "/api/corpus/sources/s1/failures": () => [
        { error: "invalid JSON from ollama: ''", count: 2, sample_chunk_ids: ["s1:0", "s1:1"] },
      ],
      "/api/corpus/sources/s1/retry-failed": () => {
        retried = true;
        return { source_id: "s1", status: "running", retried_chunks: 2 };
      },
      "/api/corpus/sources": () => (retried ? [{ ...row, failed_chunks: 0, status: "running" }] : [row]),
    });

    renderWithProviders(<CorpusView />);
    await screen.findByText("a.pdf");
    await userEvent.click(screen.getByRole("button", { name: /2 problems — why\?/ }));
    await screen.findByText(/reading helper/i);
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(retried).toBe(true));
  });

  it("warns when synthesis is enqueued but the worker is offline", async () => {
    installFetchMock({
      ...corpusHandlers(),
      "/api/corpus/synthesize": () => ({ enqueued: true, worker_online: false }),
    });

    renderWithProviders(<CorpusView />);
    await screen.findByText("My materials");
    await userEvent.click(screen.getByRole("button", { name: "Project settings" }));
    await userEvent.click(screen.getByRole("button", { name: "Build my course from materials" }));
    await screen.findByText(/Worker offline/);
  });

  it("shows error banner on 422 ingest", async () => {
    installFetchMock({
      ...corpusHandlers(),
      "/api/corpus/sources": (_path, init) => {
        if (init?.method === "POST") {
          return {
            ok: false,
            status: 422,
            statusText: "Unprocessable Entity",
            json: async () => ({ detail: "failed to load '/bad'" }),
          };
        }
        return [];
      },
    });

    renderWithProviders(<CorpusView />);
    await screen.findByText("My materials");
    await userEvent.click(screen.getByRole("tab", { name: "From a link" }));
    await userEvent.type(screen.getByLabelText(/Paste a YouTube or article link/i), "/bad");
    await userEvent.click(screen.getByRole("button", { name: "Add link" }));
    await screen.findByText(/couldn't open that file or link/i);
  });
});
