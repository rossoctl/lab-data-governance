import { useState } from 'react';
import { Button, CodeBlock, CodeBlockCode, Spinner } from '@patternfly/react-core';

import { usePayload } from '../../api/hooks';
import { DetailList } from '../DetailList';
import { ClassificationView } from '../ClassificationView';
import { DataLineageView } from '../DataLineageView';
import type { LineageState } from '../../types';

/**
 * A collapsible request/response payload. Ported from the vanilla flow view's
 * Req/Resp cells + showPayload(): a link shows the hash's first 8 chars, and
 * expanding it lazily fetches `GET /api/payloads/{hash}` and renders the
 * decoded content plus kind/hash/bytes. Fetch is gated on `open` (usePayload
 * enabled only once expanded), so an unopened payload costs nothing.
 *
 * The two derived governance facts hang off the expanded body: the payload's
 * **Classification** verdict (keyed by content hash, inlined on the payload) and
 * its **Data lineage** (keyed by the **Interaction leg** this payload sits on,
 * ADR-0027 D5 — so it is passed *in* by the panel from the one trace-scoped read
 * rather than re-fetched here per expansion).
 */
export function PayloadView({
  label,
  hash,
  lineage,
}: {
  label: string;
  hash: string;
  /**
   * What is currently known about this leg's lineage: a derived triple, "not
   * derived yet", or "the read failed" — three states, never one overloaded
   * `null` (see {@link LineageState}).
   */
  lineage: LineageState;
}) {
  const [open, setOpen] = useState(false);
  const { data, isLoading, isError } = usePayload(open ? hash : null);
  return (
    <div style={{ marginTop: '0.25rem' }}>
      <Button
        variant="link"
        isInline
        onClick={() => setOpen((o) => !o)}
        className="dg-mono"
      >
        {open ? '▼' : '▶'} {label}: {hash.slice(0, 8)}
      </Button>
      {open && (
        <div style={{ marginTop: '0.25rem' }}>
          {isLoading ? (
            <Spinner size="md" aria-label={`Loading ${label} payload`} />
          ) : isError || !data ? (
            <div style={{ color: 'var(--dg-color-error)', fontSize: '0.85rem' }}>
              Failed to load payload.
            </div>
          ) : (
            <>
              <DetailList
                pairs={[
                  ['kind', data.content_kind],
                  ['hash', data.content_hash],
                  ['bytes', String(data.byte_size)],
                ]}
              />
              <CodeBlock>
                <CodeBlockCode>
                  {data.content == null ? '(none)' : JSON.stringify(data.content, null, 2)}
                </CodeBlockCode>
              </CodeBlock>
              {/* The P-classification Classification verdict for this payload
                  (issue #80): sensitivity level, regulatory tags, identity
                  bundle, and the Findings. `null` renders as "not yet
                  classified" (the eventual-consistency window, ADR-0024),
                  distinct from a real PUBLIC / zero-Findings verdict. */}
              <div style={{ marginTop: '0.5rem' }}>
                <div style={{ fontWeight: 700, fontSize: '0.85rem' }}>Classification</div>
                <ClassificationView classification={data.classification} />
              </div>
              {/* The P-data-lineage metadata for THIS LEG (issue #119): the
                  payload's data sources, the transformations applied per source,
                  and the unordered set of entities it passed through. An
                  undelivered derivation renders as "lineage not yet computed"
                  (the eventual-consistency window, ADR-0027) and a failed read
                  as an explicit error — never as each other, and never as a
                  derived answer. */}
              <div style={{ marginTop: '0.5rem' }}>
                <div style={{ fontWeight: 700, fontSize: '0.85rem' }}>Data lineage</div>
                <DataLineageView state={lineage} />
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
