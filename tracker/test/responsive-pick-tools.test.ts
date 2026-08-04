import { describe, expect, it, vi } from "vitest";

import {
  mockPickToolsPresentation,
  watchResponsiveQuery,
  type ResponsiveMediaQuery,
} from "../src/responsive-pick-tools";

describe("responsive mock pick tools", () => {
  it("always exposes the actions on desktop without changing compact expansion", () => {
    expect(mockPickToolsPresentation(true, false)).toEqual({
      toggleExpanded: true,
      toggleHidden: true,
      toolsHidden: false,
    });
  });

  it("restores the compact disclosure state below the desktop breakpoint", () => {
    expect(mockPickToolsPresentation(false, false)).toEqual({
      toggleExpanded: false,
      toggleHidden: false,
      toolsHidden: true,
    });
    expect(mockPickToolsPresentation(false, true)).toEqual({
      toggleExpanded: true,
      toggleHidden: false,
      toolsHidden: false,
    });
  });

  it("registers a removable modern media-query listener", () => {
    const addEventListener = vi.fn();
    const removeEventListener = vi.fn();
    const query = { matches: false, addEventListener, removeEventListener };
    const listener = vi.fn();

    const stop = watchResponsiveQuery(query, listener);

    expect(addEventListener).toHaveBeenCalledWith("change", listener);
    stop();
    expect(removeEventListener).toHaveBeenCalledWith("change", listener);
  });

  it("supports and removes the legacy media-query listener seam", () => {
    const addListener = vi.fn();
    const removeListener = vi.fn();
    const query: ResponsiveMediaQuery = { matches: false, addListener, removeListener };
    const listener = vi.fn();

    const stop = watchResponsiveQuery(query, listener);

    expect(addListener).toHaveBeenCalledWith(listener);
    stop();
    expect(removeListener).toHaveBeenCalledWith(listener);
  });
});
