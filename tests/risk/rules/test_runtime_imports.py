"""The engine imports ``rules.rego`` at runtime (``FALLBACK_RULE_ID``,
``policy_version``); the processor image has no dev dependencies. Importing
the module must therefore not require ``jsonschema`` — pinned by importing
it in a subprocess whose import of ``jsonschema`` is blocked (observed live:
leg-ready crash-looped on ``ModuleNotFoundError: jsonschema``)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]

_PROBE = """
import sys
class _Block:
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in ('jsonschema', 'referencing'):
            raise ImportError(f'{name} is dev-only (blocked by test)')
        return None
sys.meta_path.insert(0, _Block())
import data_governance.risk.engine.compute  # noqa: F401
from data_governance.risk.rules.rego import FALLBACK_RULE_ID, policy_version
print(FALLBACK_RULE_ID, policy_version({'policy_id': 'p', 'version': '1'}))
"""


def test_engine_import_does_not_need_dev_only_packages():
    result = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=_REPO_ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0000 p:1"
