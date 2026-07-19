import {
  DescriptionList,
  DescriptionListGroup,
  DescriptionListTerm,
  DescriptionListDescription,
} from '@patternfly/react-core';

/**
 * A compact horizontal key/value list for the detail panels (span detail +
 * flow detail). Single-sources the `dg-detail-dl` styling contract
 * (equalised label/value font size, bold labels, tightened row gap — see
 * global.css) so the two panels can't drift. Values render monospace.
 */
export function DetailList({ pairs }: { pairs: Array<[string, string]> }) {
  return (
    <DescriptionList isCompact isHorizontal className="dg-detail-dl">
      {pairs.map(([k, v]) => (
        <DescriptionListGroup key={k}>
          <DescriptionListTerm>{k}</DescriptionListTerm>
          <DescriptionListDescription className="dg-mono">{v}</DescriptionListDescription>
        </DescriptionListGroup>
      ))}
    </DescriptionList>
  );
}
