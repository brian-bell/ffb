import { describe, expect, it } from "vitest";
import { boardNoticeHtml } from "../src/board-view";

describe("boardNoticeHtml", () => {
  it("keeps the contract-drift recovery action instead of falling back to no-board copy", () => {
    const html = boardNoticeHtml(2);
    expect(html).toContain("Board format changed.");
    expect(html).toContain("Redeploy the tracker");
    expect(html).not.toContain("No board published yet.");
  });
});
