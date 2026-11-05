"""The check recorder: every property a scenario examines becomes one row
of an unpivoted table — ``[case, id, property, expected, actual, status,
comment]`` — written to the run record as it happens, so a run can be read
as a table and two runs can be diffed by (case, id).

Statuses: ``pass`` / ``FAIL`` (an expectation, met or not), ``KNOWN`` (a
failure inside a declared gap or a filed defect, never counted green),
``recorded`` (an observation with no expectation: a count the model's plan
decides, a version number, a residual that is information).

Ids are stable names, never free text: ``forest.roots``, ``ext.rules``,
``ledger.table_only``. They are the join key across code versions, so a
renamed id is a different property.

``Checks.check`` raises on a failed expectation unless the scenario asked
for deferral (``raise_at_exit``), in which case every failure is collected
and raised together when the scenario leaves its ``with`` block — so one
known defect cannot hide the rows after it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class Row:
    case: str
    id: str
    property: str
    expected: Any
    actual: Any
    status: str
    comment: str | None = None


def _norm(v: Any) -> Any:
    """Equality that ignores container identity: sets and tuples compare as
    sorted lists; everything else as JSON-serialisable values."""
    if isinstance(v, (set, frozenset)):
        return sorted(_norm(x) for x in v)
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _norm(x) for k, x in v.items()}
    return v


class Checks:
    def __init__(self, case: str, rec, *, raise_at_exit: bool = False) -> None:
        self.case, self.rec, self.deferred = case, rec, raise_at_exit
        self.rows: list[Row] = []
        self._pending: list[str] = []

    # --- the three verbs ------------------------------------------------------

    def check(self, id: str, prop: str, expected: Any, actual: Any, *, ok: bool | None = None,
              comment: str | None = None, known: str | None = None) -> bool:
        """An expectation. ``ok`` defaults to normalised equality; pass it
        explicitly for predicates (``ok=len(x) >= 2``), then ``expected`` is
        the predicate in words. ``known`` names a filed defect or declared
        gap: the row reads KNOWN and does not raise."""
        if ok is None:
            ok = _norm(expected) == _norm(actual)
        status = "pass" if ok else ("KNOWN" if known else "FAIL")
        note = comment if ok or not known else (f"{known}; {comment}" if comment else known)
        self._add(Row(self.case, id, prop, expected, actual, status, note))
        if not ok and not known:
            msg = f"{self.case} {id}: {prop} — expected {_cell(expected)}, actual {_cell(actual)}" + (
                f" ({comment})" if comment else "")
            if self.deferred:
                self._pending.append(msg)
            else:
                raise AssertionError(msg)
        return ok

    def record(self, id: str, prop: str, actual: Any, *, comment: str | None = None) -> Any:
        """An observation with no expectation."""
        self._add(Row(self.case, id, prop, None, actual, "recorded", comment))
        return actual

    def absorb_audit(self, au, *, prefix: str = "audit") -> None:
        """Every check of an Audit as a row, verdicts mapped (PASS/KNOWN/FAIL)."""
        for c in au.checks:
            status = {"PASS": "pass", "KNOWN": "KNOWN", "FAIL": "FAIL"}[c.verdict]
            comment = f"{c.axis}" + (f"; known gap {c.known}" if c.known else "")
            self._add(Row(self.case, f"{prefix}.{c.id}", c.title, c.expected, c.observed, status, comment))

    # --- lifecycle -------------------------------------------------------------

    def _add(self, row: Row) -> None:
        self.rows.append(row)
        self.rec.write("checks.json", [asdict(r) for r in self.rows])

    def failures(self) -> list[str]:
        return list(self._pending)

    def finish(self) -> None:
        """Raise the deferred failures, if any (what leaving the ``with`` does)."""
        if self._pending:
            pending, self._pending = self._pending, []
            raise AssertionError(f"{len(pending)} expectation(s) failed:\n" + "\n".join(pending))

    def __enter__(self) -> "Checks":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.finish()


def expect(ch: Checks | None, id: str, prop: str, expected: Any, actual: Any, *, ok: bool | None = None,
           comment: str | None = None, known: str | None = None) -> None:
    """For library code that may run with or without a recorder: records the
    row when there is one, and asserts either way (a KNOWN never raises)."""
    if ch is not None:
        ch.check(id, prop, expected, actual, ok=ok, comment=comment, known=known)
        return
    if ok is None:
        ok = _norm(expected) == _norm(actual)
    assert ok or known, f"{id}: {prop} — expected {_cell(expected)}, actual {_cell(actual)}"


def record(ch: Checks | None, id: str, prop: str, actual: Any, *, comment: str | None = None) -> None:
    if ch is not None:
        ch.record(id, prop, actual, comment=comment)


def _cell(v: Any) -> str:
    s = v if isinstance(v, str) else json.dumps(_norm(v), default=str, sort_keys=True)
    return s if len(s) <= 200 else s[:197] + "…"


# --- the forest law as rows ------------------------------------------------------


def forest(ch: Checks, f, *, expect_entry: str, expect_entries: int = 1, expect_children: bool = True) -> None:
    """``shape.assert_lineage_forest`` as rows (same law, same order)."""
    ch.check("forest.derived", "interactions derived", ">= 1", f.interactions, ok=f.interactions >= 1)
    ch.check("forest.roots", "roots = injected entries", expect_entries, f.roots)
    ch.check("forest.orphans", "orphan interactions", 0, f.orphans)
    ch.check("forest.unpaired", "request spans without a response twin", 0, f.unpaired_requests)
    ch.check("forest.dup_anchors", "anchor spans mapped to two interactions", 0, f.dup_anchors)
    ch.check("forest.unstamped", "unstamped request spans = entries", expect_entries, len(f.unstamped_requests),
             comment=f"inbound parent sources {f.inbound_parent_sources}")
    ch.check("forest.entry_caller", "every entry is the entry workload", expect_entry,
             sorted({sid for _d, sid in f.unstamped_requests}) or [expect_entry],
             ok=all(sid == expect_entry for _d, sid in f.unstamped_requests))
    ch.check("forest.escaped", "other traces the entry workload started in the window", [], f.escaped_traces)
    if expect_children:
        ch.check("forest.not_collapsed", "interactions > entries (hops hang under the roots)",
                 f"> {expect_entries}", f.interactions, ok=f.interactions > expect_entries)
    else:
        ch.check("forest.probe_only", "a probe-only trace is exactly its roots", expect_entries, f.interactions,
                 ok=f.interactions == expect_entries == f.roots)
    ch.record("forest.depth", "depth histogram", f.depth_histogram)
    ch.record("forest.parent_sources", "inbound parent sources", f.inbound_parent_sources)
