import { Button, FormSelect, FormSelectOption, Switch } from '@patternfly/react-core';
import type { Entity } from '../../lib/flow';
import { isFilterActive, type GraphFilter } from '../../lib/graphFilter';

/** The risk-level choices, most severe first; each keeps that level and above. */
const RISK_CHOICES: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'critical', label: 'Critical' },
  { value: 'high', label: 'High and above' },
  { value: 'medium', label: 'Medium and above' },
  { value: 'low', label: 'Low and above' },
];

const ANY = '';

/**
 * The Execution Flow graph's filter bar (issue #220): the call paths through one
 * entity, a minimum risk level (with the risk layer on), the count of what the
 * filter keeps, and the magnifier switch. State lives with the caller.
 */
export function GraphFilterBar({
  entities,
  filter,
  onFilterChange,
  risk,
  shown,
  total,
  magnifier,
  onMagnifierChange,
}: {
  entities: readonly Entity[];
  filter: GraphFilter;
  onFilterChange: (f: GraphFilter) => void;
  /** The risk layer is on, so a risk filter can be offered. */
  risk: boolean;
  /** Interactions the filter keeps, and the trace's total. */
  shown: number;
  total: number;
  magnifier: boolean;
  onMagnifierChange: (on: boolean) => void;
}) {
  const sorted = [...entities].sort((a, b) => (a.display_name || a.id).localeCompare(b.display_name || b.id));
  const active = isFilterActive(filter);
  return (
    <div className="dg-graph-filter" role="group" aria-label="Graph filter">
      <FormSelect
        aria-label="Filter by entity"
        value={filter.entityId ?? ANY}
        onChange={(_e, v) => onFilterChange({ ...filter, entityId: v === ANY ? null : v })}
      >
        <FormSelectOption value={ANY} label="All entities" />
        {sorted.map((e) => (
          <FormSelectOption key={e.id} value={e.id} label={`Paths through ${e.display_name || e.natural_key || e.id}`} />
        ))}
      </FormSelect>
      {risk && (
        <FormSelect
          aria-label="Filter by risk level"
          value={filter.minRiskLevel ?? ANY}
          onChange={(_e, v) => onFilterChange({ ...filter, minRiskLevel: v === ANY ? null : v })}
        >
          <FormSelectOption value={ANY} label="Any risk level" />
          {RISK_CHOICES.map((c) => (
            <FormSelectOption key={c.value} value={c.value} label={c.label} />
          ))}
        </FormSelect>
      )}
      {active && (
        <>
          <span className="dg-graph-filter__count" data-testid="graph-filter-count">
            {`Showing ${shown} of ${total} interaction${total === 1 ? '' : 's'}`}
          </span>
          <Button
            variant="link"
            isInline
            onClick={() => onFilterChange({ entityId: null, minRiskLevel: null })}
          >
            Clear filter
          </Button>
        </>
      )}
      <Switch
        id="graph-magnifier"
        label="Magnifier"
        isChecked={magnifier}
        onChange={(_e, on) => onMagnifierChange(on)}
      />
    </div>
  );
}
