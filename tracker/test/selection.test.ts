import { describe, expect, it } from "vitest";
import {
  removeRecordedPlayerRows,
  setPlayerListBusy,
  syncSelectedPlayerRow,
  type PlayerSelectionList,
  type SelectablePlayerRow,
} from "../src/selection";

class FakeRow implements SelectablePlayerRow {
  readonly classes = new Set<string>(["rowA", "selectable"]);
  readonly attributes = new Map<string, string>();

  constructor(readonly key: string, selected = false) {
    if (selected) this.classes.add("selected");
    this.attributes.set("aria-pressed", String(selected));
  }

  readonly classList = {
    toggle: (name: string, force?: boolean): boolean => {
      const enabled = force ?? !this.classes.has(name);
      if (enabled) this.classes.add(name);
      else this.classes.delete(name);
      return enabled;
    },
  };

  setAttribute(name: string, value: string): void {
    this.attributes.set(name, value);
  }
}

class FakeList implements PlayerSelectionList {
  readonly selectors: string[] = [];

  constructor(readonly rows: FakeRow[]) {}

  querySelector(selector: string): FakeRow | null {
    this.selectors.push(selector);
    if (selector === ".rowA.selectable.selected") {
      return this.rows.find((row) => row.classes.has("selected")) ?? null;
    }
    const key = selector.match(/^button\[data-player-key="(.+)"\]$/)?.[1];
    return this.rows.find((row) => encodeURIComponent(row.key) === key) ?? null;
  }
}

describe("syncSelectedPlayerRow", () => {
  it("moves selection between existing rows without replacing the list", () => {
    const oldRow = new FakeRow("old:key", true);
    const newRow = new FakeRow('new/"key');
    const unrelatedRow = new FakeRow("unrelated");
    const list = new FakeList([oldRow, newRow, unrelatedRow]);

    syncSelectedPlayerRow(list, newRow.key);

    expect(oldRow.classes.has("selected")).toBe(false);
    expect(oldRow.attributes.get("aria-pressed")).toBe("false");
    expect(newRow.classes.has("selected")).toBe(true);
    expect(newRow.attributes.get("aria-pressed")).toBe("true");
    expect(unrelatedRow.attributes.get("aria-pressed")).toBe("false");
    expect(list.selectors).toEqual([
      ".rowA.selectable.selected",
      `button[data-player-key="${encodeURIComponent(newRow.key)}"]`,
    ]);
  });

  it("clears the selected row without scanning for a replacement", () => {
    const selectedRow = new FakeRow("selected", true);
    const list = new FakeList([selectedRow, new FakeRow("other")]);

    syncSelectedPlayerRow(list, null);

    expect(selectedRow.classes.has("selected")).toBe(false);
    expect(selectedRow.attributes.get("aria-pressed")).toBe("false");
    expect(list.selectors).toEqual([".rowA.selectable.selected"]);
  });
});

describe("removeRecordedPlayerRows", () => {
  it("removes every equivalent identity and updates each affected tier in place", () => {
    const removed: string[] = [];
    const element = (name: string, textContent: string, attributes: Array<[string, string]> = []) => {
      const values = new Map(attributes);
      return {
        getAttribute: (attribute: string) => values.get(attribute) ?? null,
        setAttribute: (attribute: string, value: string) => values.set(attribute, value),
        textContent,
        remove: () => removed.push(name),
      };
    };
    const canonicalRow = element("canonical row", "A. Brown");
    const fallbackRow = element("fallback row", "A Brown");
    const unrelatedRow = element("unrelated row", "Other");
    const tierFour = element("tier 4", "Tier 4 · WR · 3 left", [["data-tier-count", "3"]]);
    const tierFive = element("tier 5", "Tier 5 · WR · 1 left", [["data-tier-count", "1"]]);
    const elements = new Map([
      [`button[data-player-key="${encodeURIComponent("canonical")}"]`, canonicalRow],
      [`button[data-player-key="${encodeURIComponent("manual:a-brown")}"]`, fallbackRow],
      [`button[data-player-key="${encodeURIComponent("unrelated")}"]`, unrelatedRow],
      ['.trule[data-tier-key="4"]', tierFour],
      ['.trule[data-tier-key="5"]', tierFive],
    ]);
    const list = {
      querySelector: (selector: string) => elements.get(selector) ?? null,
    };
    const players = [
      { key: "canonical", name: "A. Brown", pos: "WR", team: "PHI", tier: 4 },
      { key: "manual:a-brown", name: "A Brown", pos: "WR", team: "PHI", tier: 5 },
      { key: "unrelated", name: "Other", pos: "WR", team: "PHI", tier: 5 },
    ];

    expect(removeRecordedPlayerRows(list, players, players[0])).toBe(true);
    expect(removed).toEqual(["canonical row", "fallback row", "tier 5"]);
    expect(tierFour.getAttribute("data-tier-count")).toBe("2");
    expect(tierFour.textContent).toBe("Tier 4 · WR · 2 left");
    expect(unrelatedRow.textContent).toBe("Other");
  });
});

describe("setPlayerListBusy", () => {
  it("disables the existing list in place while a pick write is pending", () => {
    const attributes = new Map<string, string>();
    const list = {
      toggleAttribute: (name: string, force?: boolean) => {
        if (force) attributes.set(name, "");
        else attributes.delete(name);
      },
      setAttribute: (name: string, value: string) => attributes.set(name, value),
    };

    setPlayerListBusy(list, true);
    expect(attributes.get("inert")).toBe("");
    expect(attributes.get("aria-busy")).toBe("true");

    setPlayerListBusy(list, false);
    expect(attributes.has("inert")).toBe(false);
    expect(attributes.get("aria-busy")).toBe("false");
  });
});
