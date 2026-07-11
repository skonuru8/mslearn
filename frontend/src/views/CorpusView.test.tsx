import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CorpusView } from "./CorpusView";
import { uploadSource } from "../api/client";
import { corpusHandlers, installFetchMock } from "../test/fetchMock";
import { renderWithProviders } from "../test/renderWithProviders";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    uploadSource: vi.fn(),
  };
});

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

  it("retries a failed source that never produced chunks", async () => {
    const row = {
      source_id: "s1",
      ref: "a.pdf",
      role: "spine",
      status: "failed",
      total_chunks: 0,
      done_chunks: 0,
      failed_chunks: 0,
      rejected_chunks: 0,
      error: "SSL error",
      ts: 1,
    };
    let retried = false;
    installFetchMock({
      ...corpusHandlers([row]),
      "/api/corpus/sources/s1/retry": () => {
        retried = true;
        return { source_id: "s1", mode: "reload" };
      },
      "/api/corpus/sources": () =>
        retried ? [{ ...row, status: "chunking", error: null }] : [row],
    });

    renderWithProviders(<CorpusView />);
    await screen.findByText("a.pdf");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(retried).toBe(true));
  });

  it("warns when synthesis is enqueued but the worker is offline", async () => {
    installFetchMock({
      ...corpusHandlers(),
      "/api/corpus/synthesize": () => ({
        enqueued: true,
        already_running: false,
        worker_online: false,
      }),
    });

    renderWithProviders(<CorpusView />);
    await screen.findByText("My materials");
    await userEvent.click(screen.getByRole("button", { name: "Project settings" }));
    await userEvent.click(screen.getByRole("button", { name: "Build my course from materials" }));
    await screen.findByText(/Worker offline/);
  });

  it("says the course is already building instead of queueing a duplicate", async () => {
    installFetchMock({
      ...corpusHandlers(),
      "/api/corpus/synthesize": () => ({
        enqueued: false,
        already_running: true,
        worker_online: true,
      }),
    });

    renderWithProviders(<CorpusView />);
    await screen.findByText("My materials");
    await userEvent.click(screen.getByRole("button", { name: "Project settings" }));
    await userEvent.click(screen.getByRole("button", { name: "Build my course from materials" }));
    await screen.findByText("Already building your course — hang tight.");
  });

  it("defaults the main-course checkbox off once a spine source exists", async () => {
    installFetchMock(
      corpusHandlers([
        {
          source_id: "s1",
          ref: "book.pdf",
          role: "spine",
          status: "ready",
          total_chunks: 3,
          done_chunks: 3,
          failed_chunks: 0,
          rejected_chunks: 0,
          error: null,
          ts: 1,
        },
      ]),
    );

    renderWithProviders(<CorpusView />);
    await screen.findByText("book.pdf");

    const checkbox = screen.getByRole("checkbox", {
      name: /Is this your main book or course/i,
    }) as HTMLInputElement;
    await waitFor(() => expect(checkbox.checked).toBe(false));
    expect(screen.getByText(/You already have a main course/i)).toBeTruthy();
  });

  it("accepts multiple files and reflects the count", async () => {
    installFetchMock(corpusHandlers([]));
    renderWithProviders(<CorpusView />);
    await screen.findByText("My materials");

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input.multiple).toBe(true);

    const files = [
      new File(["a"], "one.pdf", { type: "application/pdf" }),
      new File(["b"], "two.pdf", { type: "application/pdf" }),
    ];
    await userEvent.upload(input, files);

    await screen.findByText("2 files selected");
    expect(screen.getByRole("button", { name: "Add 2 files" })).toBeTruthy();
  });

  it("shows a main-source picker when multiple files are selected", async () => {
    installFetchMock(corpusHandlers([]));
    renderWithProviders(<CorpusView />);
    await screen.findByText("My materials");

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const files = [
      new File(["a"], "one.pdf", { type: "application/pdf" }),
      new File(["b"], "two.pdf", { type: "application/pdf" }),
      new File(["c"], "three.pdf", { type: "application/pdf" }),
    ];
    await userEvent.upload(input, files);

    await screen.findByText(/which file is the main source/i);
    const radios = screen.getAllByRole("radio") as HTMLInputElement[];
    expect(radios).toHaveLength(3);
    expect(radios[0]!.checked).toBe(true);
    expect(radios[1]!.checked).toBe(false);
    expect(radios[2]!.checked).toBe(false);

    await userEvent.click(radios[1]!);
    expect(radios[0]!.checked).toBe(false);
    expect(radios[1]!.checked).toBe(true);
    expect(radios[2]!.checked).toBe(false);
  });

  it("preserves file selection and link text when switching tabs", async () => {
    installFetchMock(corpusHandlers([]));
    renderWithProviders(<CorpusView />);
    await screen.findByText("My materials");

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const files = [
      new File(["a"], "one.pdf", { type: "application/pdf" }),
      new File(["b"], "two.pdf", { type: "application/pdf" }),
    ];
    await userEvent.upload(input, files);
    await screen.findByText("2 files selected");
    await screen.findByText(/which file is the main source/i);

    await userEvent.click(screen.getByRole("tab", { name: "From a link" }));
    await userEvent.type(
      screen.getByLabelText(/Paste a YouTube or article link/i),
      "https://example.com/article",
    );

    await userEvent.click(screen.getByRole("tab", { name: "From my computer" }));
    expect(screen.getByText("2 files selected")).toBeInTheDocument();
    expect(screen.getAllByRole("radio")).toHaveLength(2);

    await userEvent.click(screen.getByRole("tab", { name: "From a link" }));
    expect(
      (screen.getByLabelText(/Paste a YouTube or article link/i) as HTMLInputElement).value,
    ).toBe("https://example.com/article");
  });

  it("uploads multiple files concurrently with per-file roles", async () => {
    const fetchMock = installFetchMock(corpusHandlers([]));
    const calls: Array<{ name: string; role: string }> = [];
    const resolvers: Array<(value: { source_id: string; stored_path: string }) => void> = [];
    vi.mocked(uploadSource).mockImplementation((file, role) => {
      calls.push({ name: file.name, role });
      return new Promise((resolve) => {
        resolvers.push(resolve);
      });
    });

    renderWithProviders(<CorpusView />);
    await screen.findByText("My materials");

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const files = [
      new File(["a"], "one.pdf", { type: "application/pdf" }),
      new File(["b"], "two.pdf", { type: "application/pdf" }),
    ];
    await userEvent.upload(input, files);
    await screen.findByText(/which file is the main source/i);
    const radios = screen.getAllByRole("radio");
    await userEvent.click(radios[1]!);

    await userEvent.click(screen.getByRole("button", { name: "Add 2 files" }));

    // Both uploads must be in flight before either resolves — proves
    // concurrency, not a sequential await-per-file loop.
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(resolvers).toHaveLength(2);
    expect(calls).toEqual([
      { name: "one.pdf", role: "supplement" },
      { name: "two.pdf", role: "spine" },
    ]);

    const sourceGetCallsBefore = fetchMock.mock.calls.filter(([url, init]: [string, RequestInit?]) => {
      const path = url.startsWith("http") ? new URL(url).pathname : url;
      return path === "/api/corpus/sources" && init?.method === undefined;
    }).length;

    resolvers.forEach((resolve) => resolve({ source_id: "s-new", stored_path: "/tmp/x" }));

    await waitFor(() => {
      const after = fetchMock.mock.calls.filter(([url, init]: [string, RequestInit?]) => {
        const path = url.startsWith("http") ? new URL(url).pathname : url;
        return path === "/api/corpus/sources" && init?.method === undefined;
      }).length;
      expect(after).toBeGreaterThan(sourceGetCallsBefore);
    });
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
