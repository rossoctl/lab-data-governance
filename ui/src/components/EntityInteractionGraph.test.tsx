import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithProviders } from '../test/renderWithProviders';
import { EntityInteractionGraph } from './EntityInteractionGraph';

// React Flow needs layout measurements jsdom lacks, so we assert the container
// mounts (data-testid) with data and that the empty-state shows without
// entities — the node/edge derivation itself is covered by entityGraph.test.ts.

describe('EntityInteractionGraph', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('shows an empty state when the trace has no entities', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async () => ({
      ok: true, status: 200, json: async () => ({ entities: [], interactions: [] }),
    }));
    renderWithProviders(<EntityInteractionGraph traceId="T1" />);
    await waitFor(() => expect(screen.getByText('No entities')).toBeInTheDocument());
  });

  it('mounts the graph canvas when entities are present', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.endsWith('/entities')) {
        return {
          ok: true, status: 200,
          json: async () => ({
            entities: [
              { id: 'e1', kind: 'agent', natural_key: 'agent:(p,a)', display_name: 'agent-a', detected_from: '' },
            ],
          }),
        };
      }
      return { ok: true, status: 200, json: async () => ({ interactions: [] }) };
    });
    renderWithProviders(<EntityInteractionGraph traceId="T1" />);
    await waitFor(() => expect(screen.getByTestId('entity-graph')).toBeInTheDocument());
  });
});
