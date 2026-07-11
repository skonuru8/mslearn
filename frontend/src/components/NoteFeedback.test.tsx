import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { NoteFeedback } from "./NoteFeedback";

function emptyFeedbackResponse() {
  return { ok: true, json: async () => ({}) };
}

describe("NoteFeedback", () => {
  it("saves a thumbs-down rating with a tag and comment", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(emptyFeedbackResponse())
      .mockResolvedValueOnce({ ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetchMock);

    render(<NoteFeedback conceptId="k1" />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    await userEvent.click(screen.getByRole("button", { name: "👎" }));
    await userEvent.click(screen.getByLabelText("Too shallow"));
    await userEvent.type(screen.getByLabelText(/feedback comment/i), "needs more depth");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/api/study/concepts/k1/feedback");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string);
    expect(body.helpful).toBe(false);
    expect(body.tags).toEqual(["too_shallow"]);
    expect(body.comment).toBe("needs more depth");

    await screen.findByText("Saved");
  });

  it("prefills from the GET endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        helpful: true,
        tags: ["repetitive"],
        comment: "a bit repetitive",
        guide_hash: "h1",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<NoteFeedback conceptId="k2" />);

    await screen.findByDisplayValue("a bit repetitive");
    expect(screen.getByRole("button", { name: "👍" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("Repetitive")).toBeChecked();
    await screen.findByText("Saved");
  });
});
