export const MOCK_DESKTOP_QUERY = "(min-width: 1024px)";

export interface ResponsiveMediaQuery {
  matches: boolean;
  addEventListener?: (type: "change", listener: () => void) => void;
  removeEventListener?: (type: "change", listener: () => void) => void;
  addListener?: (listener: () => void) => void;
  removeListener?: (listener: () => void) => void;
}

export interface MockPickToolsPresentation {
  toggleExpanded: boolean;
  toggleHidden: boolean;
  toolsHidden: boolean;
}

export function mockPickToolsPresentation(
  desktop: boolean,
  compactExpanded: boolean,
): MockPickToolsPresentation {
  return {
    toggleExpanded: desktop || compactExpanded,
    toggleHidden: desktop,
    toolsHidden: !desktop && !compactExpanded,
  };
}

export function watchResponsiveQuery(
  query: ResponsiveMediaQuery,
  listener: () => void,
): () => void {
  if (query.addEventListener && query.removeEventListener) {
    query.addEventListener("change", listener);
    return () => query.removeEventListener?.("change", listener);
  }
  query.addListener?.(listener);
  return () => query.removeListener?.(listener);
}
