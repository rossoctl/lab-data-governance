import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { Routes, Route } from 'react-router-dom';
import { renderWithProviders } from './test/renderWithProviders';
import { useParams } from 'react-router-dom';
import { useEntities } from './api/hooks';

function Probe() {
  const { traceId = 'NONE' } = useParams<{ traceId: string }>();
  const { status } = useEntities(traceId);
  return <div data-testid="p">{traceId}|{status}</div>;
}

describe('dbg', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      console.log('FETCH:', url);
      return { ok: true, status: 200, json: async () => ({ entities: [] }) };
    }));
  });
  it('resolves under a matched route', async () => {
    renderWithProviders(
      <Routes><Route path="/traces/:traceId/:view" element={<Probe />} /></Routes>,
      { route: '/traces/T1/flow' },
    );
    await waitFor(() => expect(screen.getByTestId('p').textContent).toContain('success'));
    console.log('RESULT:', screen.getByTestId('p').textContent);
  });
});
