import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { StudyGuide } from "../api/types";
import { InteractiveGuide } from "./InteractiveGuide";

function baseGuide(overrides: Partial<StudyGuide> = {}): StudyGuide {
  return {
    concept_id: "c",
    title: "Merge sort",
    tl_dr: { text: "Fast sort.", claims: ["c3"] },
    skeleton: ["Cost"],
    sections: [
      {
        id: "s1",
        title: "Cost",
        items: [{ kind: "claim", text: "O(n log n).", claims: ["c3"] }],
      },
    ],
    disagreements: [],
    open_questions: [],
    ...overrides,
  };
}

describe("InteractiveGuide", () => {
  it("renders sections, no raw claim ids, and a Sources footer", () => {
    const guide = baseGuide();
    const citations = [{ claim_id: "c3", quote: "n log n", page: 12 }];
    render(
      <InteractiveGuide guide={guide} progress={{}} citations={citations} onToggleSection={() => {}} />,
    );
    expect(screen.getByRole("heading", { name: "Cost" })).toBeInTheDocument();
    expect(screen.queryByText(/c3/)).not.toBeInTheDocument(); // no raw id
    expect(screen.getAllByText(/Sources/i).length).toBeGreaterThan(0);
  });

  it("shows a numbered superscript for a cited claim with no matching citation, without crashing", () => {
    const guide = baseGuide({
      tl_dr: { text: "Fast sort.", claims: [] },
      sections: [
        {
          id: "s1",
          title: "Cost",
          items: [{ kind: "claim", text: "O(n log n).", claims: ["c-missing"] }],
        },
      ],
    });
    render(<InteractiveGuide guide={guide} progress={{}} citations={[]} onToggleSection={() => {}} />);
    expect(screen.getByText("1", { selector: "sup" })).toBeInTheDocument();
    expect(screen.queryByText(/c-missing/)).not.toBeInTheDocument();
  });

  it("renders disagreements as a two-column compare with a classification badge", () => {
    const guide = baseGuide({
      disagreements: [
        {
          summary: "Sources disagree on stability.",
          classification: "genuine_debate",
          a: { label: "Paper A", text: "Merge sort is always stable.", claims: ["c1"] },
          b: { label: "Paper B", text: "Some implementations are not stable.", claims: ["c2"] },
        },
      ],
    });
    render(<InteractiveGuide guide={guide} progress={{}} citations={[]} onToggleSection={() => {}} />);
    expect(screen.getByText("Paper A")).toBeInTheDocument();
    expect(screen.getByText("Paper B")).toBeInTheDocument();
    expect(screen.getByText(/genuine debate/i)).toBeInTheDocument();
  });

  it("never renders a raw claim-id-shaped label in the disagreement section", () => {
    const guide = baseGuide({
      disagreements: [
        {
          summary: "Sources disagree on stability.",
          classification: "genuine_debate",
          a: {
            label: "Position A",
            text: "Merge sort is always stable.",
            claims: ["7f3a1c2b-89de-4f01-9c3e-abc123def456"],
          },
          b: {
            label: "Position B",
            text: "Some implementations are not stable.",
            claims: ["a1b2c3d4-5678-90ef-1234-56789abcdef0"],
          },
        },
      ],
    });
    render(<InteractiveGuide guide={guide} progress={{}} citations={[]} onToggleSection={() => {}} />);
    // No "claim <id>"-shaped text anywhere in the disagreement section (the regression this guards against).
    expect(screen.queryByText(/claim\s+[0-9a-f-]{6,}/i)).not.toBeInTheDocument();
    // The raw claim ids themselves must not leak as visible text either.
    expect(screen.queryByText(/7f3a1c2b-89de-4f01-9c3e-abc123def456/)).not.toBeInTheDocument();
    expect(screen.queryByText(/a1b2c3d4-5678-90ef-1234-56789abcdef0/)).not.toBeInTheDocument();
    // The neutral labels drive the headings instead.
    expect(screen.getByText("Position A")).toBeInTheDocument();
    expect(screen.getByText("Position B")).toBeInTheDocument();
  });

  it("renders open questions in a visually-distinct advisory box", () => {
    const guide = baseGuide({ open_questions: ["Does this hold for linked lists?"] });
    render(<InteractiveGuide guide={guide} progress={{}} citations={[]} onToggleSection={() => {}} />);
    expect(screen.getByText(/open questions/i)).toBeInTheDocument();
    expect(screen.getByText("Does this hold for linked lists?")).toBeInTheDocument();
  });

  it("calls onToggleSection when the reviewed checkbox is toggled", async () => {
    const guide = baseGuide();
    const onToggleSection = vi.fn();
    render(
      <InteractiveGuide guide={guide} progress={{}} citations={[]} onToggleSection={onToggleSection} />,
    );
    await userEvent.click(screen.getByRole("checkbox", { name: /reviewed/i }));
    expect(onToggleSection).toHaveBeenCalledWith("s1", true);
  });

  it("renders the labeled interpretation block", () => {
    const guide = baseGuide({
      interpretation: [
        { angle: "verdict", text: "This holds only for short horizons.", claims: ["k-abc123"] },
      ],
    });
    render(<InteractiveGuide guide={guide} progress={{}} citations={[]} onToggleSection={() => {}} />);
    expect(screen.getByText(/Model's analysis/i)).toBeInTheDocument();
    expect(screen.getByText("This holds only for short horizons.")).toBeInTheDocument();
    expect(screen.queryByText(/k-abc123/)).not.toBeInTheDocument(); // no raw id
  });

  it("no interpretation block when empty", () => {
    const guide = baseGuide({ interpretation: [] });
    render(<InteractiveGuide guide={guide} progress={{}} citations={[]} onToggleSection={() => {}} />);
    expect(screen.queryByText(/Model's analysis/i)).not.toBeInTheDocument();
  });

  it("shows an N / total reviewed progress readout driven by the progress prop", () => {
    const guide = baseGuide({
      sections: [
        { id: "s1", title: "Cost", items: [{ kind: "claim", text: "a", claims: ["c1"] }] },
        { id: "s2", title: "Merge step", items: [{ kind: "claim", text: "b", claims: ["c2"] }] },
      ],
      skeleton: ["Cost", "Merge step"],
    });
    render(
      <InteractiveGuide
        guide={guide}
        progress={{ s1: true, s2: false }}
        citations={[]}
        onToggleSection={() => {}}
      />,
    );
    expect(screen.getByText("1 / 2 reviewed")).toBeInTheDocument();
  });
});
