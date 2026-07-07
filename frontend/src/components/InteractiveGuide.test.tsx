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
