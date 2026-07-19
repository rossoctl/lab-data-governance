import KeyIcon from '@patternfly/react-icons/dist/esm/icons/key-icon';
import InfoCircleIcon from '@patternfly/react-icons/dist/esm/icons/info-circle-icon';
import CircleIcon from '@patternfly/react-icons/dist/esm/icons/circle-icon';
import MinusIcon from '@patternfly/react-icons/dist/esm/icons/minus-icon';
import { roleMeta, type RoleIconKind } from '../lib/flow';

const ICONS: Record<Exclude<RoleIconKind, 'none'>, typeof KeyIcon> = {
  key: KeyIcon,
  info: InfoCircleIcon,
  dot: CircleIcon,
  minus: MinusIcon,
};

/**
 * A span-evidence `role` rendered as its glyph (see `roleMeta` for the
 * mapping/weight rationale): 🔑 key for the creating span (anchor /
 * discovered_via), ⓘ info, ● dot for a repeat identity sighting, – for the
 * throwaway connector. Hovering shows the human role text (title tooltip); the
 * same text is also rendered visually-hidden so screen readers and tests can
 * read the role. An unrecognised role falls back to its plain text, no glyph.
 */
export function RoleIcon({ role }: { role: string }) {
  const { label, icon } = roleMeta(role);
  if (icon === 'none') return <span>{label}</span>;
  const Glyph = ICONS[icon];
  return (
    <span title={label} className={`dg-role-icon dg-role-icon--${icon}`}>
      <Glyph aria-hidden="true" />
      <span className="pf-v5-screen-reader">{label}</span>
    </span>
  );
}
