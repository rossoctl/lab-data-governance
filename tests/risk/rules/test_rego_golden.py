"""Golden-file test: the shipped catalog compiles byte-for-byte to a
committed Rego file (issue #173).

This test detects **drift**, not correctness — it does not evaluate the
Rego in OPA (``test_rego_opa.py``'s ``@pytest.mark.opa`` suite does that
against a real container) and it does not re-derive the expected clauses
from first principles (``test_rego.py`` does that on isolated fixture
policies). It exists so that any change to ``rules_source.json`` or to the
compiler's output shape shows up as a PR diff against
``fixtures/rules_source.rego`` — a reviewer sees exactly what changed in
the emitted Rego, rather than the change being invisible until an OPA test
happens to catch it (or doesn't).

When the shipped catalog or the compiler's emitted Rego legitimately
changes, regenerate the golden file:

    uv run python3 -c "
    from data_governance.risk.rules import catalog
    from data_governance.risk.rules.rego import compile_policy
    policy = catalog.load_rules_source()
    rego = compile_policy(policy)
    open('tests/risk/rules/fixtures/rules_source.rego', 'w').write(rego)
    "

and review the resulting diff like any other code change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from data_governance.risk.rules import catalog
from data_governance.risk.rules.rego import compile_policy

_GOLDEN = Path(__file__).parent / "fixtures" / "rules_source.rego"


@pytest.fixture(autouse=True)
def _fresh_catalog():
    catalog.reload()
    yield
    catalog.reload()


def test_shipped_catalog_compiles_byte_for_byte_to_the_golden_file():
    policy = catalog.load_rules_source()
    rego = compile_policy(policy)
    expected = _GOLDEN.read_text(encoding="utf-8")
    assert rego == expected, (
        "Compiled Rego drifted from the golden file. If this is an "
        "intentional change (catalog edit or compiler change), regenerate "
        "fixtures/rules_source.rego per this module's docstring and review "
        "the diff."
    )


def test_golden_file_is_committed_and_non_empty():
    assert _GOLDEN.is_file()
    assert _GOLDEN.read_text(encoding="utf-8").strip()
