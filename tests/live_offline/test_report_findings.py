"""Offline guard for tests/live/report.py (default suite, no cluster). It lives
outside tests/live because that directory's session fixtures are autouse and
need a cluster.

report.py recognises known findings by substrings of assertion messages. This
pins each key to the source file that must still contain it, so rewording an
assertion without updating FINDINGS fails here instead of silently reporting
"Known findings reproduced: none"."""

from pathlib import Path

import pytest

from tests.live import report

LIVE = Path(__file__).parent.parent / "live"
SOURCES = {
    "PII from another session": LIVE / "test_travel_live.py",
    "entity set mismatch": LIVE / "shape.py",
    "a cut stream reads ok": LIVE / "test_travel_live.py",
    "scored as sent to the external destination": LIVE / "test_travel_live.py",
}


@pytest.mark.parametrize("key", sorted(report.FINDINGS))
def test_each_finding_key_is_still_an_assertion_message(key: str) -> None:
    assert key in SOURCES, f"FINDINGS key {key!r} has no source registered here"
    assert key in SOURCES[key].read_text(encoding="utf-8"), (
        f"{SOURCES[key].name} no longer contains {key!r}; "
        f"update report.FINDINGS in the same change")


def test_every_registered_source_is_a_finding() -> None:
    assert set(SOURCES) == set(report.FINDINGS)
