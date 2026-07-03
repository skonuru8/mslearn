import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ChatView } from "./ChatView";

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
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ turns: [] }) })
      .mockResolvedValueOnce(
        streamResponse([
          'data: {"delta":"Hello "}\n\n',
          'data: {"delta":"[claim:c1]"}\n\n',
          'data: {"done":true,"citations":["c1"]}\n\n',
        ]),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(<ChatView />);
    await userEvent.type(screen.getByLabelText(/Question/i), "What is cache?");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => {
      expect(screen.getByText(/Hello \[claim:c1\]/)).toBeInTheDocument();
    });
    await screen.findByText("c1");
  });
});
