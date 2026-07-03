import { describe, expect, it } from "vitest";
import { splitTeachMarkdown } from "./teachMarkdown";

describe("splitTeachMarkdown", () => {
  it("returns full markdown when no tension section", () => {
    const md = "## Explanation\n\nText [claim:c1]";
    expect(splitTeachMarkdown(md)).toEqual({ main: md, tension: null });
  });

  it("splits at tension heading", () => {
    const md =
      "## Explanation\n\nA [claim:c1]\n\n## Where sources disagree\n\nSide A [claim:c1] vs Side B [claim:c2]";
    const { main, tension } = splitTeachMarkdown(md);
    expect(main).toBe("## Explanation\n\nA [claim:c1]");
    expect(tension).toBe("## Where sources disagree\n\nSide A [claim:c1] vs Side B [claim:c2]");
  });
});
