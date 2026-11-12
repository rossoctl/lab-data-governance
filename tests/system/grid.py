"""The unit grid: units as rows, legs as columns, drawn twice: the expected grid
(the case's edges; ● must_see, ○ must_miss) above the observed one (what the run
produced, inherited labels on; ● where ``Case.labelled`` says the model labelled it).

    ●  present and labelled by the classifier here
    ○  present, the classifier did not label it here
    ━  an edge into this cell (expected, or drawn by the run)
    ┈  the unit is present here and was present earlier, but no edge arrives (expected or drawn)
    ·  absent
"""

from __future__ import annotations

from collections.abc import Callable

from .cases import Case
from .engine import Observation

def _width(legs) -> int:
    return 12 if len(legs) <= 8 else 7


def _present(case: Case, leg_index: int, unit: str) -> bool:
    leg = next(l for l in case.line.legs if l.index == leg_index)
    return unit in leg.units


def render_grid(case: Case, on: Observation | None, off: Observation | None) -> str:
    """The expected grid above the observed one; in each, one block per
    conversation, leg numbers running on across conversations."""
    obs = on or off

    def observed_found(leg_index: int, unit: str) -> bool:
        c = obs.legs[leg_index].classification if obs and leg_index in obs.legs else None
        return bool(c) and case.labelled(unit, c["findings"])

    lines = ["expected", _grid(case, case.edges, lambda i, u: u in case.must_see.get(i, ())),
             "", "observed", _grid(case, obs.edges, observed_found) if obs else "(not run)"]
    sink = case.line.sink
    if sink:
        v_on = on.verdict if on else "?"
        v_off = off.verdict if off else "?"
        lines.append(f"verdict at {sink.index}: expected on={case.verdict.get('on')} / off={case.verdict.get('off')}, "
                     f"observed on={v_on} / off={v_off}")
    return "\n".join(lines)


def _grid(case: Case, edges: set, found: Callable[[int, str], bool]) -> str:
    return "\n\n".join(_block(case, conv.legs, edges, found, c) for c, conv in enumerate(case.line.conversations))


def _block(case: Case, legs, edges: set, found: Callable[[int, str], bool], conversation: int) -> str:
    W = _width(legs)
    units = list(case.units)
    lines: list[str] = []
    if len(case.line.conversations) > 1:
        lines.append(f"conversation {conversation + 1}")
    head1 = " " * 14 + "".join(f"{(f'{l.index}-{l.index + 1}' if l.invisible else l.index):<{W}}" for l in legs)
    head2 = " " * 14 + "".join(f"{(l.label() + ('⚑' if l.sink else '')) if not l.invisible else '..' + l.to + '..':<{W}}" for l in legs)
    lines += [head1, head2]
    for unit in units:
        label = f"{case.units[unit]:<8}{unit:<6}"
        cells: list[str] = []
        seen_before = any(_present(case, l.index, unit) for l in case.line.legs if l.index < legs[0].index)
        for leg in legs:
            if leg.invisible:
                cells.append(f"{'┈' * (W - 1) if seen_before else '':<{W}}")
                continue
            present = _present(case, leg.index, unit)
            edge_in = any(u == unit and to == leg.index for u, _, to in edges)
            if present:
                dot = "●" if found(leg.index, unit) else "○"
                lead = "━" if edge_in else ("┈" if seen_before else " ")
                cells.append(f"{lead * (W - 1)}{dot}" if (edge_in or seen_before) else f"{dot:<{W}}")
                seen_before = seen_before or present
            else:
                cells.append(f"{'·':<{W}}")
        lines.append(label + "".join(cells))
    return "\n".join(lines)
