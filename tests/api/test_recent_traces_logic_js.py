"""Node-driven tests for the recent-traces UI's data-layer helpers.

The UI's two non-trivial JS behaviours — trace-id dedupe and the
missing-parent filter toggle (issue #13) — live in
``data_governance/api/ui/recent_traces_logic.js`` so they can be
exercised by ``node`` without booting a browser engine.

Tests are skipped if ``node`` is not on PATH so the suite remains
runnable on minimal CI images.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_LOGIC_JS = (
    Path(__file__).resolve().parents[2]
    / "data_governance"
    / "api"
    / "ui"
    / "recent_traces_logic.js"
)


def _have_node() -> bool:
    return shutil.which("node") is not None


pytestmark = pytest.mark.skipif(
    not _have_node(), reason="node is not installed; skipping UI-logic tests"
)


def _run_js(script: str) -> str:
    """Run a Node snippet that requires the logic module and prints
    JSON to stdout. Returns the stdout text."""
    full = (
        f"const M = require({json.dumps(str(_LOGIC_JS))});\n"
        f"{script}\n"
    )
    result = subprocess.run(
        ["node", "-e", full],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"node exited {result.returncode}\nstderr:\n{result.stderr}\n"
        f"stdout:\n{result.stdout}"
    )
    return result.stdout


def test_dedupe_keeps_highest_seq_anchor_per_trace():
    """A trace appearing twice across pages with different anchors:
    the higher-seq anchor wins, the older one is dropped."""
    out = _run_js(
        """
        const rows = [
          { trace_id: 'A', span_id: 'old', seq: 1, parent_id: 'X',
            started_at: '2026-05-01T12:00:00Z' },
          { trace_id: 'A', span_id: 'new', seq: 5, parent_id: null,
            started_at: '2026-05-01T11:00:00Z' },
          { trace_id: 'B', span_id: 'b1', seq: 2, parent_id: null,
            started_at: '2026-05-01T13:00:00Z' },
        ];
        const out = M.dedupeByTraceId(rows);
        process.stdout.write(JSON.stringify(out));
        """
    )
    deduped = json.loads(out)
    by_trace = {r["trace_id"]: r for r in deduped}
    assert by_trace["A"]["span_id"] == "new"
    assert by_trace["A"]["seq"] == 5
    assert by_trace["B"]["span_id"] == "b1"


def test_dedupe_orders_by_started_at_desc():
    """Output preserves the listing-root started_at desc order produced
    by the server (PROJECT.md §6 Path 3)."""
    out = _run_js(
        """
        const rows = [
          { trace_id: 'A', span_id: 'a', seq: 1, parent_id: null,
            started_at: '2026-05-01T10:00:00Z' },
          { trace_id: 'B', span_id: 'b', seq: 2, parent_id: null,
            started_at: '2026-05-01T13:00:00Z' },
          { trace_id: 'C', span_id: 'c', seq: 3, parent_id: null,
            started_at: '2026-05-01T11:00:00Z' },
        ];
        const out = M.dedupeByTraceId(rows);
        process.stdout.write(JSON.stringify(out.map(r => r.trace_id)));
        """
    )
    assert json.loads(out) == ["B", "C", "A"]


def test_missing_parent_filter_off_returns_all_rows():
    out = _run_js(
        """
        const rows = [
          { trace_id: 'A', parent_id: null },
          { trace_id: 'B', parent_id: 'missing' },
        ];
        process.stdout.write(JSON.stringify(M.applyMissingParentFilter(rows, false)));
        """
    )
    assert len(json.loads(out)) == 2


def test_missing_parent_filter_on_hides_orphan_listing_roots():
    """Filter on -> only listing roots whose parent_id is null remain."""
    out = _run_js(
        """
        const rows = [
          { trace_id: 'A', parent_id: null },
          { trace_id: 'B', parent_id: 'missing' },
          { trace_id: 'C', parent_id: null },
        ];
        process.stdout.write(JSON.stringify(M.applyMissingParentFilter(rows, true)));
        """
    )
    kept = json.loads(out)
    assert [r["trace_id"] for r in kept] == ["A", "C"]


def test_format_time_24_utc_uses_zero_padded_24_hour_clock():
    out = _run_js(
        """
        const samples = [
          M.formatTime24Utc('2026-05-01T00:05:09Z'),
          M.formatTime24Utc('2026-05-01T13:45:30.123456+00:00'),
          M.formatTime24Utc('not-a-date'),
        ];
        process.stdout.write(JSON.stringify(samples));
        """
    )
    assert json.loads(out) == ["00:05:09", "13:45:30", "not-a-date"]
