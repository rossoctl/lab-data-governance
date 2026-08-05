import { Label, LabelGroup, Title } from '@patternfly/react-core';
import { Table, Thead, Tbody, Tr, Th, Td } from '@patternfly/react-table';
import { ExclamationTriangleIcon } from '@patternfly/react-icons';
import type { Classification, Finding } from '../types';

/**
 * The **Classification** verdict for one **Payload** (CONTEXT.md), rendered
 * inside the flow view's Req/Resp payload cells (issue #80). Read-only surface
 * over the inlined nullable `classification` field of `GET /api/payloads/{hash}`
 * (ADR-0024).
 */
export function ClassificationView({
  classification,
}: {
  classification: Classification | null | undefined;
}) {
  // The eventual-consistency window (ADR-0024): the payload exists but
  // P-classification has not written a verdict yet. Rendered as a distinct,
  // verdict-less state — deliberately NOT a PUBLIC badge, so it never reads as
  // a real clean verdict (every payload is classified uniformly, so a null
  // means "not yet processed", never "processed but clean" — CONTEXT.md).
  if (classification == null) {
    return (
      <div style={{ color: 'var(--dg-color-muted)', fontStyle: 'italic', fontSize: '0.85rem' }}>
        Not yet classified
      </div>
    );
  }

  const { regulatory_tags, contains_identity_bundle, findings } = classification;

  return (
    <div>
      <SensitivityBadge level={classification.sensitivity_level} />

      {contains_identity_bundle && (
        <Label
          isCompact
          color="red"
          icon={<ExclamationTriangleIcon />}
          style={{ marginLeft: '0.375rem' }}
        >
          Identity bundle
        </Label>
      )}

      {regulatory_tags.length > 0 && (
        <div style={{ marginTop: '0.375rem' }}>
          <LabelGroup categoryName="Regulatory tags" numLabels={99}>
            {regulatory_tags.map((tag) => (
              <Label key={tag} isCompact color="grey">
                {tag}
              </Label>
            ))}
          </LabelGroup>
        </div>
      )}

      <Title headingLevel="h5" size="md" style={{ marginTop: '0.5rem' }}>
        Findings
      </Title>
      <FindingsTable findings={findings} />
    </div>
  );
}

/**
 * The **Findings** the NER model detected (CONTEXT.md): each row is the
 * finding's detected *type* — its NER tag (`SSN`, `EMAIL`, … — deliberately
 * NOT an **Entity**, which is the interaction participant) — and the flagged
 * *text region* it matched. An empty list is a real zero-Findings verdict
 * (distinct from the null "not yet classified" state the parent guards), so it
 * is stated explicitly rather than rendered as nothing.
 */
function FindingsTable({ findings }: { findings: Finding[] }) {
  if (findings.length === 0) {
    return (
      <div style={{ color: 'var(--dg-color-muted)', fontSize: '0.85rem', marginTop: '0.25rem' }}>
        No findings — no sensitive text detected.
      </div>
    );
  }
  return (
    <Table aria-label="Findings" variant="compact">
      <Thead>
        <Tr>
          <Th>Type</Th>
          <Th>Flagged text</Th>
          <Th>Region</Th>
        </Tr>
      </Thead>
      <Tbody>
        {findings.map((f, i) => (
          <Tr key={`${f.entity_type}-${f.start}-${f.end}-${i}`}>
            <Td dataLabel="Type">
              <Label isCompact color="orange">
                {f.entity_type}
              </Label>
            </Td>
            <Td dataLabel="Flagged text" className="dg-mono">
              {f.text}
            </Td>
            <Td dataLabel="Region" className="dg-mono" style={{ color: 'var(--dg-color-muted)' }}>
              {f.start}–{f.end}
            </Td>
          </Tr>
        ))}
      </Tbody>
    </Table>
  );
}

/**
 * The escalation-ordered sensitivity palette (CONTEXT.md **Classification**):
 * PUBLIC (calm green) → INTERNAL (blue) → CONFIDENTIAL (orange) → RESTRICTED
 * (alarm red). An unknown level falls back to grey so a taxonomy the UI hasn't
 * caught up with (the model-derived set churns — issue #78) still renders.
 */
const SENSITIVITY_COLOR: Record<string, React.ComponentProps<typeof Label>['color']> = {
  PUBLIC: 'green',
  INTERNAL: 'blue',
  CONFIDENTIAL: 'orange',
  RESTRICTED: 'red',
};

/** The document-level sensitivity level as a color-escalated badge. */
function SensitivityBadge({ level }: { level: string }) {
  return (
    <Label isCompact color={SENSITIVITY_COLOR[level] ?? 'grey'}>
      {level}
    </Label>
  );
}
