"""Docs-wiring guards for ``dg.sh`` as the documented deploy entry point (#185).

A deliberately SMALL set of pure-text assertions over the tracked Markdown — no
cluster, no subprocess. Their job is to catch the code/doc divergence class that
review keeps missing because the prose reads plausibly: the docs must present
``dg.sh`` with the right verb surface, cross-link the design doc + BOTH ADRs, and
describe the SHIPPED ``instrument`` contract — kit-only, no-sidecar-only (any
existing sidecar is skipped), per ADR-0032 Decision #2 (revised). The retired
three-row / edit-in-place table must not reappear as current behaviour.

This file was trimmed on the code-review that fixed that divergence: the earlier
version's key assertion only checked that "proxy-sidecar"/"envoy-only" *appeared*
— which the STALE three-row text satisfied, so it passed while the docs were
wrong. The negative assertion below (no in-place row, no egressEnforcement
warning) is the guard that would actually have caught it. Brittle
incidental-string / ordering checks were dropped: they gave false confidence and
duplicate what review covers.

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
    for p in (DESIGN_DOC, ADR_0031, ADR_0032):
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


def test_playbook_cross_links_both_adrs() -> None:
    """ADR-0031 (why non-reversible / no mode switch) AND ADR-0032 (why build on
    the cortex kit) are both discoverable from the playbook."""
    text = _read(PLAYBOOK)
    assert "0031-non-reversible-namespace-lineage-activation.md" in text, (
        "deploy playbook must link ADR-0031"
    )
    assert "0032-dg-sh-builds-on-cortex-lineage-attach-kit.md" in text, (
        "deploy playbook must link ADR-0032"
    )


# ---------------------------------------------------------------------------
# The load-bearing guard: the playbook describes the SHIPPED instrument contract
# (kit-only, no-sidecar-only; existing sidecars skipped — ADR-0032 Decision #2,
# revised), NOT the retired three-row edit-in-place table.
# ---------------------------------------------------------------------------


def test_playbook_notes_instrument_is_no_sidecar_only_and_skips_existing() -> None:
    text = _read(PLAYBOOK)
    low = text.lower()
    # instrument acts on no-sidecar entities only …
    assert "no-sidecar" in low or "no sidecar" in low, (
        "must note instrument wires lineage onto no-sidecar entities only"
    )
    # … and skips any entity that already has a sidecar (proxy or envoy).
    assert re.search(r"skip", low), (
        "must note that an entity that already has a sidecar is skipped"
    )
    assert "proxy" in low and "envoy" in low, (
        "must name both sidecar types (proxy/envoy) that are skipped"
    )
    # The retired three-row remnant must NOT reappear as CURRENT behaviour. The
    # tell of the old table is a "-in-place" ROW label (e.g. "proxy-sidecar-in-place"
    # / "envoy-sidecar-in-place") or a claim that instrument WARNS on
    # egressEnforcement: none (that warning went away with the proxy row —
    # ADR-0032 Decision #3). Prose explaining WHY an in-place edit was rejected is
    # fine; a documented in-place row or warning path is not.
    assert not re.search(r"-in-place|sidecar in place", low), (
        "the retired in-place-edit row must not be documented as current behaviour "
        "(ADR-0032 Decision #2 revised it to no-sidecar-only)"
    )
    assert not re.search(r"instrument\b[^.]*\bwarn", low), (
        "the retired egressEnforcement warning path must not be documented "
        "(ADR-0032 Decision #3: it went away with the proxy row)"
    )
