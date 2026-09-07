"""``python -m data_governance.risk.rules.compile`` is the deploy path's
compiler entry point (``deploy/create-opa-configmap.sh``): its stdout must
be byte-for-byte the bundle :func:`compile_bundle` returns, with nothing
else (no logging, no trailing banner) mixed into the stream that becomes
the ``opa-policy`` ConfigMap's ``data_governance.rego``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from data_governance.risk.rules import compile as compile_module

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_module_entry_point_writes_exactly_the_compiled_bundle_to_stdout():
    result = subprocess.run(
        [sys.executable, "-m", "data_governance.risk.rules.compile"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == compile_module.compile_bundle()
    assert result.stdout.startswith("package data_governance\n")
    assert "default policy_decision" in result.stdout
