"""Docs-wiring guards for ``dg.sh`` as the documented deploy entry point (#185).

A deliberately SMALL set of pure-text assertions over the tracked Markdown — no
cluster, no subprocess. Their job is to catch the code/doc divergence class that
review keeps missing because the prose reads plausibly: the docs must present
``dg.sh`` with the right verb surface, cross-link the design doc + the ADRs, and
describe the shipped ``instrument`` contract: only existing, trusted Rossoctl
AuthBridge proxies are reconciled; bare workloads fail closed before mutation.

These match the ``pathlib`` + ``read_text`` conventions the rest of
``tests/deploy/`` uses (see ``test_dg_sh_component.py``'s ``REPO_ROOT``).
"""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"
K8S_README = REPO_ROOT / "deploy" / "k8s" / "README.md"
DESIGN_DOC = REPO_ROOT / "docs" / "cli.md"
ADR_0031 = REPO_ROOT / "docs" / "adr" / "0031-non-reversible-namespace-lineage-activation.md"
ADR_0032 = REPO_ROOT / "docs" / "adr" / "0032-dg-sh-builds-on-cortex-lineage-attach-kit.md"
ADR_0033 = REPO_ROOT / "docs" / "adr" / "0033-dg-sh-vendors-lineage-attach-proxy-default-one-trace.md"


# The deploy playbook = the k8s README (the file README.md points at for "the
# full procedure"). The cross-links + the instrument/cortex notes live there.
PLAYBOOK = K8S_README


def _read(p: Path) -> str:
    assert p.is_file(), f"expected {p} to exist"
    return p.read_text()


# ---------------------------------------------------------------------------
# The cross-linked design docs exist (with the CORRECT ADR numbers — the PR
# renumbered to 0031/0032 to clear main's ADR-0030 collision).
# ---------------------------------------------------------------------------


def test_design_docs_exist() -> None:
    for p in (DESIGN_DOC, ADR_0031, ADR_0032, ADR_0033):
        assert p.is_file(), f"cross-linked design doc missing: {p}"


# ---------------------------------------------------------------------------
# dg.sh is the PRIMARY component entry point, with all THREE verbs (the review
# caught cli.md calling it "two verbs" — this pins the count in the playbook).
# ---------------------------------------------------------------------------


def test_readme_presents_dg_sh_component_install() -> None:
    text = _read(README)
    assert re.search(r"dg\.sh\s+component\s+install", text), (
        "README must present `dg.sh component install` as the install path"
    )


def test_playbook_presents_all_three_component_verbs() -> None:
    """install AND uninstall AND status via ``dg.sh component`` — the primary
    lifecycle surface (three verbs, not two)."""
    text = _read(PLAYBOOK)
    for verb in ("install", "uninstall", "status"):
        assert re.search(rf"dg\.sh\s+component\s+{verb}", text), (
            f"deploy playbook must document `dg.sh component {verb}`"
        )


# ---------------------------------------------------------------------------
# Cross-links resolve: the design doc AND both ADRs (by their real filenames).
# ---------------------------------------------------------------------------


def test_playbook_cross_links_design_doc() -> None:
    assert "cli.md" in _read(PLAYBOOK), "deploy playbook must link docs/cli.md"


def test_playbook_cross_links_the_adrs() -> None:
    """ADR-0031 and the current existing-proxy ADR-0033 are discoverable."""
    text = _read(PLAYBOOK)
    assert "0031-non-reversible-namespace-lineage-activation.md" in text, (
        "deploy playbook must link ADR-0031"
    )
    assert "0033-dg-sh-vendors-lineage-attach-proxy-default-one-trace.md" in text, (
        "deploy playbook must link ADR-0033 (the current instrument contract)"
    )


# ---------------------------------------------------------------------------
# The load-bearing guard: the playbook describes the shipped existing-proxy
# contract and does not advertise sidecar injection.
# ---------------------------------------------------------------------------


def test_playbook_documents_existing_proxy_only_contract() -> None:
    text = _read(PLAYBOOK)
    low = text.lower()
    assert "rossoctl-managed" in low
    assert "authbridge-proxy" in low
    assert "never injects or replaces a sidecar" in low
    assert "fail" in low and "closed" in low
