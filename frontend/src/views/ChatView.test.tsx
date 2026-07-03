import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ChatView } from "./ChatView";
import { corpusHandlers, installFetchMock } from "../test/fetchMock";
import { renderWithProviders } from "../test/renderWithProviders";

function streamResponse(chunks: string[]) {
  const encoder = new TextEncoder();
  let index = 0;
  return {
    ok: true,
    body: {
      getReader: () => ({
        read: async () => {
          if (index >= chunks.length) {
            return { done: true, value: undefined };
          }
          const value = encoder.encode(chunks[index++]);
          return { done: false, value };
        },
      }),
    },
  };
}

describe("ChatView", () => {
  it("streams deltas then citation chips", async () => {
    sessionStorage.clear();
    installFetchMock({
      ...corpusHandlers([{ source_id: "s1", status: "done", done_chunks: 1 }]),
      "/api/chat/sessions/": () => ({ turns: [] }),
      "/api/chat": () =>
        streamResponse([
          'data: {"delta":"Hello "}\n\n',
          'data: {"delta":"[claim:c1]"}\n\n',
          'data: {"done":true,"citations":["c1"]}\n\n',
        ]),
    });

    renderWithProviders(<ChatView />);
    await userEvent.type(screen.getByLabelText(/Your question/i), "What is cache?");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => {
      expect(screen.getByText(/Hello \[claim:c1\]/)).toBeInTheDocument();
    });
    await screen.findByText("c1");
  });
});
