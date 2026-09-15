"""Docs-wiring guards for ``dg.sh`` as the documented deploy entry point (#185).

A deliberately SMALL set of pure-text assertions over the tracked Markdown — no
cluster, no subprocess. Their job is to catch the code/doc divergence class that
review keeps missing because the prose reads plausibly: the docs must present
``dg.sh`` with the right verb surface, cross-link the design doc + the ADRs, and
describe the SHIPPED ``instrument`` contract — the ADR-0033 owner-split (#245):
a no-sidecar entity is INJECTED (proxy by default, envoy when the namespace is
already envoy-configured), and an entity that already has a sidecar is APPENDED
to in place (best-effort) rather than skipped. The retired ADR-0032 "skip any
existing sidecar" / no-sidecar-only text must not stand as current behaviour.

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
    """ADR-0031 (why non-reversible / no mode switch) AND ADR-0033 (the vendored,
    proxy-default owner-split that superseded ADR-0032) are both discoverable from
    the playbook."""
    text = _read(PLAYBOOK)
    assert "0031-non-reversible-namespace-lineage-activation.md" in text, (
        "deploy playbook must link ADR-0031"
    )
    assert "0033-dg-sh-vendors-lineage-attach-proxy-default-one-trace.md" in text, (
        "deploy playbook must link ADR-0033 (the current instrument contract)"
    )


# ---------------------------------------------------------------------------
# The load-bearing guard: the playbook describes the SHIPPED instrument contract
# — the ADR-0033 owner-split (#245). A no-sidecar entity is INJECTED (proxy by
# default; envoy when the namespace is already envoy-configured); an entity that
# ALREADY has a sidecar is APPENDED to in place (best-effort). The retired
# ADR-0032 "skip any existing sidecar" / no-sidecar-only text must not stand.
# ---------------------------------------------------------------------------


def test_playbook_notes_instrument_owner_split() -> None:
    text = _read(PLAYBOOK)
    low = text.lower()
    # The default no-sidecar injection is the PROXY sidecar …
    assert "proxy" in low, "must document the default proxy lineage sidecar"
    # … envoy is the alternative when the namespace is already envoy-configured.
    assert "envoy" in low, "must name the envoy alternative"
    # … and an existing sidecar is APPENDED to in place, not skipped.
    assert re.search(r"append|in[- ]place", low), (
        "must document the in-place append onto an entity that already has a sidecar"
    )
    # The retired ADR-0032 contract must NOT stand as current behaviour: a claim
    # that instrument acts on no-sidecar entities ONLY, or that it SKIPS any
    # entity that already has a sidecar. (Prose explaining the HISTORY — that an
    # earlier design skipped, now revised — is fine; a current "skips existing
    # sidecars" / "no-sidecar only" contract is not.)
    assert not re.search(r"no-sidecar (entities )?only|only.{0,20}no[- ]sidecar", low), (
        "the retired no-sidecar-only contract must not stand as current behaviour "
        "(ADR-0033 owner-split: existing sidecars are appended to, not skipped)"
    )
    assert not re.search(r"skip(s|ped)?\b[^.\n]{0,60}\b(already has a sidecar|existing sidecar|proxy or envoy)", low), (
        "the retired 'skip any existing sidecar' contract must not stand as "
        "current behaviour (ADR-0033 appends in place)"
    )
