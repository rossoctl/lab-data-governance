import { createContext, useContext } from 'react';
import type { TrackedValue } from '../../lib/labelTrack';

/**
 * How the Label lineage tab's tick boxes reach the page that owns the graphs. The
 * page (`FlowTables` on the trace page, `RiskTraceDetailPage`) provides the set
 * of tracked values and the two moves; the tab, five components down, ticks and
 * unticks. `available` is false under the default (no provider: a page with no
 * graph to paint, tests), and the tab then shows no tick boxes at all.
 */
export interface LabelTrack {
  available: boolean;
  tracked: ReadonlyMap<string, TrackedValue>;
  /** Tick (`value`) or untick (`null`) one value. */
  set: (key: string, value: TrackedValue | null) => void;
  /** Replace the whole set (track all, track the rule-named ones, clear). */
  replace: (values: readonly TrackedValue[]) => void;
}

export const NO_LABEL_TRACK: LabelTrack = {
  available: false,
  tracked: new Map(),
  set: () => {},
  replace: () => {},
};

export const LabelTrackContext = createContext<LabelTrack>(NO_LABEL_TRACK);

export function useLabelTrack(): LabelTrack {
  return useContext(LabelTrackContext);
}
