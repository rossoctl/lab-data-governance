import { createContext, useContext } from 'react';

/**
 * How a lineage hop tells the flow view which interaction the pointer is on,
 * so the sequence diagram can light that interaction's legs (`InteractionDiagram`'s
 * `hoveredId`). A context rather than a prop: the hop sits five components under
 * `FlowTables`, and none of the four in between has a reason to know about hover.
 * The default is a no-op, so the hop renders the same wherever there is no diagram
 * to light (tests).
 */
export const HopHoverContext = createContext<(interactionId: string | null) => void>(() => {});

export function useHopHover(): (interactionId: string | null) => void {
  return useContext(HopHoverContext);
}
