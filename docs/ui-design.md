# UI Design Spec — Data Governance v1

**Status:** Approved (pending user sign-off — final AC item)  
**Implements:** [Issue #11](https://github.com/s-and-p-team/data-governance/issues/11)  
**Unblocks:** Issue #12 (recent-traces view), Issue #13 (trace-tree view)

---

## Design system

The Data Governance UI is built on **PatternFly 5** (`@patternfly/react-core` v5.4,
`@patternfly/react-icons` v5.4), matching the visual language of
[`rossoctl/ui-v2`](https://github.com/rossoctl/rossoctl/tree/main/rossoctl/ui-v2).
All CSS custom properties below are the PF5 / rossoctl tokens already defined in
`global.css`. No new icon library is introduced — all glyphs come from
`@patternfly/react-icons`.

Design tokens are specified in §9 below as the content of `src/styles/spans.css`.
Implementing slices create that file and import it once at app entry — there is no
UI source tree to commit to yet.

---

## 1. Per-`kind` icon set (trace-tree view)

Each span row in the trace tree shows a 16 × 16 px icon to the left of the span
name. All icons are from `@patternfly/react-icons`. Color is
`var(--pf-v5-global--Color--200)` (muted) so the icon supports, rather than
competes with, the span name.

| `kind` value | PF5 icon component | Rationale |
|---|---|---|
| `INTERNAL` | `CubeIcon` | generic building block, no network boundary |
| `SERVER` | `ServerIcon` | receiving end of an RPC/HTTP call |
| `CLIENT` | `ArrowRightIcon` | initiating a call outward |
| `PRODUCER` | `ShareAltIcon` | publishing / fanning out a message |
| `CONSUMER` | `InboxIcon` | receiving a queued message |

```tsx
// src/components/SpanKindIcon.tsx
import {
  CubeIcon,
  ServerIcon,
  ArrowRightIcon,
  ShareAltIcon,
  InboxIcon,
} from '@patternfly/react-icons';

const ICONS: Record<string, React.ComponentType<{ color?: string; style?: React.CSSProperties }>> = {
  INTERNAL: CubeIcon,
  SERVER:   ServerIcon,
  CLIENT:   ArrowRightIcon,
  PRODUCER: ShareAltIcon,
  CONSUMER: InboxIcon,
};

export const SpanKindIcon: React.FC<{ kind: string }> = ({ kind }) => {
  const Icon = ICONS[kind] ?? CubeIcon;
  return (
    <Icon
      color="var(--pf-v5-global--Color--200)"
      style={{ width: 16, height: 16, flexShrink: 0 }}
    />
  );
};
```

**Position:** inline-flex, `gap: 8px`, icon left of span name text.  
**Size:** 16 px (matches PF5 `sm` icon scale used in `BuildProgressView`).

---

## 2. Error badge (trace-tree view)

Shown on each span where `error IS TRUE`. Uses the PF5 `Label` component
(`pf-m-red`) to match the danger-label treatment in `global.css`.

```tsx
import { Label } from '@patternfly/react-core';
import { ExclamationCircleIcon } from '@patternfly/react-icons';

// error badge
<Label color="red" icon={<ExclamationCircleIcon />} isCompact>
  Error
</Label>
```

- **Color:** `pf-m-red` → background `#b1380b`, text `#ffffff` (defined in
  `global.css` `.pf-v5-c-label.pf-m-red`).
- **Shape:** PF5 `Label` default — pill with `border-radius: 30px`, `padding:
  2px 8px`.
- **`status_message` placement:** rendered immediately below the badge in a
  `<div>` styled with `font-size: 0.8em; color: var(--pf-v5-global--Color--200);
  margin-top: 2px;`. It is only shown when `status_message` is non-empty.

```css
/* spans.css */
.dg-error-message {
  font-size: 0.8em;
  color: var(--pf-v5-global--Color--200);
  margin-top: 2px;
}
```

---

## 3. Descendant-error badge (trace-tree view)

Shown on every **ancestor** of an `error IS TRUE` span (computed client-side as
the user expands subtrees). Must be visually quieter than the error badge per
PROJECT.md §8.

```tsx
import { Label } from '@patternfly/react-core';
import { ExclamationTriangleIcon } from '@patternfly/react-icons';

// descendant-error badge
<Label color="gold" icon={<ExclamationTriangleIcon />} isCompact>
  Child error
</Label>
```

- **Color:** `pf-m-gold` (warning yellow) → background `#fcf7de`, text `#151515`.
  Yellow reads as "caution, not the source" — distinct from red while still
  signaling something is wrong inside.
- **Icon:** `ExclamationTriangleIcon` (triangle warning) vs. the circle used for
  the direct error badge — different glyph reinforces the "indirect / propagated"
  meaning.
- **Text:** `"Child error"` (two words, keeps it short on narrow rows).
- **Relationship to error badge:** same `isCompact` Label, but `pf-m-gold` vs
  `pf-m-red`, triangle icon vs. circle icon. At a glance: red = "here", yellow =
  "inside".

```css
/* spans.css — no additional CSS needed; pf-m-gold is already in global.css */
```

---

## 4. Listing-row error count badge (recent-traces view)

Shown on a listing row when `counts[trace_id].error_count > 0`.

```tsx
import { Label } from '@patternfly/react-core';
import { ExclamationCircleIcon } from '@patternfly/react-icons';

<Label color="red" icon={<ExclamationCircleIcon />} isCompact>
  {errorCount} error{errorCount !== 1 ? 's' : ''}
</Label>
```

- **Same visual language as the per-span error badge** (same `pf-m-red` Label,
  same `ExclamationCircleIcon`). The count is the only difference — the listing
  row badge is a roll-up of the same information, so identical styling reinforces
  that relationship.
- **Text:** `"N error"` / `"N errors"` (pluralised).
- **Position:** rightmost content cell in the listing row, after the
  `in_window / total` count.

---

## 5. Missing-parent badge (recent-traces view)

Shown when the listing root is an orphan — specifically, a span whose `parent_id`
is non-null but references a span **not present in the loaded set** (the real root
has not arrived yet). This is distinct from a normal non-root span, which has a
`parent_id` that resolves within the loaded set.

```tsx
import { Label } from '@patternfly/react-core';
import { QuestionCircleIcon } from '@patternfly/react-icons';

<Label color="gold" icon={<QuestionCircleIcon />} isCompact>
  Missing parent
</Label>
```

- **Color:** `pf-m-gold` — same warning palette as the descendant-error badge, but
  a different icon and text so the two are never confused. Gold signals "something
  is incomplete / uncertain" without screaming "error".
- **Icon:** `QuestionCircleIcon` (question-mark circle) — conveys the root is unknown,
  not that it failed. (`UnknownIcon` was not available in `@patternfly/react-icons`
  v5.4; `QuestionCircleIcon` is the FA5 equivalent and is already used in `ui-v2`.)
- **Text:** `"Missing parent"`.
- **Position:** after the error count badge in the listing row. If there is no
  error count badge the missing-parent badge occupies that slot.

---

## 6. Greyed-out treatment (listing rows, out-of-window roots)

Applied to rows where `in_time_window = false`.

```css
/* spans.css */
.dg-row--out-of-window {
  opacity: 0.45;
}

.dg-row--out-of-window:hover,
.dg-row--out-of-window:focus-within {
  opacity: 0.7;
}
```

- **Opacity:** `0.45` — enough fade to push the row to background without making
  it unreadable. All columns (`service_name`, `name`, timestamps, badges) fade
  uniformly; the user can still read the trace exists.
- **Hover / focus:** raises to `0.7` on `:hover` and `:focus-within` so both
  pointer and keyboard users can inspect the row comfortably.
- **No color shift:** opacity-only treatment; no hue desaturation. Keeps the
  implementation a single CSS class and avoids needing per-element overrides.
- **Badges and text remain legible:** `0.45` opacity on a white/`#212427` table
  background keeps all PF5 label colors readable at arm's length.

---

## 7. `in_window / total` count display

Format: **`N / M`** where N = `in_window_count`, M = `total_count`.

```tsx
<span className="dg-window-count">
  {inWindow} / {total}
</span>
```

```css
/* spans.css */
.dg-window-count {
  font-variant-numeric: tabular-nums;
  color: var(--pf-v5-global--Color--200);   /* muted — secondary info */
  font-size: 0.875em;
}
```

- **Separator:** ` / ` (space-slash-space), not `of` or `:`, to match the issue
  spec phrasing.
- **Color:** `var(--pf-v5-global--Color--200)` (`#6a6e73` light / `#c9c9c9` dark)
  — secondary text, subordinate to the span name.
- **Tabular nums:** prevents the number column from jiggling as page cursor
  advances.

---

## 8. Listing row layout (recent-traces view, page-size 20)

PF5 `Table` with `isStriped={false}`, `borders={true}`. Column order:

| # | Column | Width | Notes |
|---|---|---|---|
| 1 | Service | `15%` | `service_name`; render `—` when NULL |
| 2 | Name | `35%` | span `name` |
| 3 | Started | `15%` | `started_at` formatted as `HH:mm:ss` with full ISO timestamp in a PF5 `Tooltip` |
| 4 | Spans | `10%` | `in_window / total` (§7), right-aligned |
| 5 | Status | `25%` | error count badge (§4) and/or missing-parent badge (§5), left-aligned; empty cell if neither |

Row height: PF5 default compact (`isCompact` on `Table`).  
Hover: `var(--pf-v5-c-table--tr--hover--BackgroundColor)` (already wired in
`global.css`).  
Clickable row: entire `<tr>` is a button (`onClick` on the row component) that
opens the trace tree. The `<tr>` must also carry `role="button"` and
`tabIndex={0}` (or use PF5 `Tr` with `isClickable`) so keyboard users can reach
and activate it.  
Out-of-window rows: `.dg-row--out-of-window` class on `<tr>` (§6).

```tsx
// column definitions (Th)
<Th width={15}>Service</Th>
<Th width={35}>Name</Th>
<Th width={15}>Started</Th>
<Th width={10}>Spans</Th>
<Th width={25}>Status</Th>
```

---

## 9. Design tokens reference (`src/styles/spans.css`)

```css
/* Data Governance — span / trace UI tokens */
/* Import once in main.tsx after global.css */

.dg-error-message {
  font-size: 0.8em;
  color: var(--pf-v5-global--Color--200);
  margin-top: 2px;
}

.dg-row--out-of-window {
  opacity: 0.45;
}

.dg-row--out-of-window:hover,
.dg-row--out-of-window:focus-within {
  opacity: 0.7;
}

.dg-window-count {
  font-variant-numeric: tabular-nums;
  color: var(--pf-v5-global--Color--200);
  font-size: 0.875em;
}
```

---

## Acceptance criteria checklist

- [x] Per-`kind` icon chosen for each of `INTERNAL`, `SERVER`, `CLIENT`,
      `PRODUCER`, `CONSUMER` — all from `@patternfly/react-icons`
- [x] Error badge visual spec landed — `pf-m-red` Label, `ExclamationCircleIcon`,
      `status_message` below
- [x] Descendant-error badge visual spec landed — `pf-m-gold` Label,
      `ExclamationTriangleIcon`, "Child error", visibly distinct from error badge
- [x] Listing-row error count badge visual spec landed — same `pf-m-red` Label as
      per-span badge, count pluralised
- [x] Missing-parent badge visual spec landed — `pf-m-gold` Label,
      `QuestionCircleIcon`, "Missing parent"
- [x] Greyed-out treatment specified — `opacity: 0.45`, raises to `0.7` on hover
- [x] `in_window / total` display format chosen — `N / M`, tabular nums, muted color
- [x] Listing row layout sketched — 5-column PF5 Table, column order and widths
      specified, page-size-20 default
- [x] Design artifacts checked into repo at `docs/ui-design.md` (CSS token content
      specified in §9; file created by implementing slice)
- [ ] **User has reviewed and approved the decisions** ← final sign-off needed
