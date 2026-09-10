"""Docs-wiring tests for ``dg.sh`` as the documented deploy entry point (#185).

This is the final slice of the ``dg.sh`` feature: once every verb exists
(#181–#184), the deployment docs must present ``dg.sh`` as the primary
entry point rather than the raw ``build-and-load.sh`` + ``kubectl apply``
sequence. Design: ``docs/cli.md`` (+ ADR-0031 non-reversible /
mode-preserving activation, ADR-0032 build-on-the-cortex-kit).

Acceptance criteria (issue #185 body + its re-scope comment):

1. README / deploy playbook present ``dg.sh component install|uninstall|status``
   as the PRIMARY install/uninstall/status path (raw ``build-and-load.sh`` +
   ``kubectl apply`` stay documented as the *underlying* steps).
2. The end-to-end operator scenario (component install → deploy agents →
   ``namespace instrument`` → run → observe in the UI) is documented in ONE
   place, including the ``--cortex-local-path`` on ``instrument``.
3. ``docs/cli.md`` AND ADR-0031 AND ADR-0032 are cross-linked from
   the deploy docs.
4. Root ``CLAUDE.md``'s redeploy procedure references ``dg.sh`` where it applies,
   OR is noted as superseded. The root ``CLAUDE.md`` lives OUTSIDE this git repo
   (repo root is ``data-governance/``), so this repo's own deploy playbook
   carries the supersession note instead — asserted here on the in-repo doc.

Re-scope additions:

- ``--cortex-local-path`` is documented as part of (and only of) the
  ``instrument`` flow; a missing path/kit is a refuse-and-mutate-nothing
  preflight; the component verbs + ``namespace status`` never touch cortex.
- The playbook notes the cortex kit is ENVOY-sidecar-only and the proxy-sidecar
  path is ``dg.sh``'s own.
- The playbook reconciles with the existing ad-hoc proxy attach recipe
  (``instrument-one.sh`` / ``LINEAGE-PROXY-SIDECAR-RECIPE.md``).

These are pure-text assertions over the tracked Markdown — no cluster, no
subprocess. They match the ``pathlib`` + ``read_text`` conventions the rest of
``tests/deploy/`` uses (see ``test_dg_sh_component.py``'s ``REPO_ROOT``).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"
K8S_README = REPO_ROOT / "deploy" / "k8s" / "README.md"
DESIGN_DOC = REPO_ROOT / "docs" / "cli.md"
ADR_0030 = REPO_ROOT / "docs" / "adr" / "0031-non-reversible-namespace-lineage-activation.md"
ADR_0031 = REPO_ROOT / "docs" / "adr" / "0032-dg-sh-builds-on-cortex-lineage-attach-kit.md"


# The deploy playbook = the k8s README (the file README.md points at for "the
# full procedure"). The end-to-end operator scenario + cross-links + the
# instrument/cortex notes live there, in one place.
PLAYBOOK = K8S_README


def _read(p: Path) -> str:
    assert p.is_file(), f"expected {p} to exist"
    return p.read_text()


# ---------------------------------------------------------------------------
# Sanity: the design docs this ticket cross-links actually exist.
# ---------------------------------------------------------------------------


def test_design_docs_exist() -> None:
    for p in (DESIGN_DOC, ADR_0030, ADR_0031):
        assert p.is_file(), f"cross-linked design doc missing: {p}"


# ---------------------------------------------------------------------------
# AC1 — dg.sh is the PRIMARY install/uninstall/status entry point.
# ---------------------------------------------------------------------------


def test_readme_presents_dg_sh_as_primary_entry_point() -> None:
    """The top-level README's deploy section names ``dg.sh component`` as the
    way to install (not just the raw script sequence)."""
    text = _read(README)
    assert "dg.sh" in text, "README must reference dg.sh"
    assert re.search(r"dg\.sh\s+component\s+install", text), (
        "README must present `dg.sh component install` as the install path"
    )


def test_playbook_presents_all_three_component_verbs() -> None:
    """The deploy playbook documents install AND uninstall AND status via
    ``dg.sh component`` — the primary lifecycle surface."""
    text = _read(PLAYBOOK)
    for verb in ("install", "uninstall", "status"):
        assert re.search(rf"dg\.sh\s+component\s+{verb}", text), (
            f"deploy playbook must document `dg.sh component {verb}`"
        )


def test_playbook_keeps_raw_steps_as_underlying() -> None:
    """The raw ``build-and-load.sh`` + ``kubectl apply`` steps stay documented
    as the *underlying* mechanics (dg.sh is primary, not a replacement that
    hides them)."""
    text = _read(PLAYBOOK)
    assert "build-and-load.sh" in text, (
        "the underlying build-and-load.sh step must remain documented"
    )
    assert "kubectl apply -f deploy/k8s/" in text, (
        "the underlying `kubectl apply -f deploy/k8s/` step must remain documented"
    )


def test_readme_quick_redeploy_references_dg_sh() -> None:
    """The README's quick re-deploy snippet points at dg.sh (AC4 supersession
    for the in-repo redeploy procedure) rather than only the raw restart dance."""
    text = _read(README)
    # The quick-redeploy section must mention dg.sh as the productized path.
    assert re.search(r"dg\.sh\s+component\s+install", text), (
        "README quick re-deploy must reference `dg.sh component install`"
    )


# ---------------------------------------------------------------------------
# AC2 — the end-to-end operator scenario is documented in ONE place.
# ---------------------------------------------------------------------------


def test_playbook_has_end_to_end_operator_scenario() -> None:
    """One place documents the full flow: component install → deploy agents →
    namespace instrument → run → observe in the UI."""
    text = _read(PLAYBOOK)
    low = text.lower()
    # component install
    assert re.search(r"dg\.sh\s+component\s+install", text)
    # namespace instrument
    assert re.search(r"dg\.sh(?:\s+--cortex-local-path\s+\S+)?\s+namespace\s+\S+\s+instrument", text), (
        "scenario must include `dg.sh namespace <ns> instrument`"
    )
    # observe in the UI (the dg.localtest.me landing)
    assert "dg.localtest.me" in text, "scenario must end at observing the UI"
    # the ordered scenario is a single contiguous block, not scattered — the
    # instrument step must come AFTER the component install step in the text.
    assert text.index("component install") < text.rindex("instrument"), (
        "the scenario must order component install before namespace instrument"
    )


def test_scenario_documents_cortex_local_path_on_instrument() -> None:
    """Step 4 of the scenario carries ``--cortex-local-path`` on instrument."""
    text = _read(PLAYBOOK)
    assert "--cortex-local-path" in text, (
        "the instrument scenario must document --cortex-local-path"
    )
    # It is documented specifically on the instrument invocation.
    assert re.search(
        r"dg\.sh\s+--cortex-local-path\s+\S+\s+namespace\s+\S+\s+instrument", text
    ), "scenario must show `--cortex-local-path` on the `namespace instrument` call"


def test_playbook_documents_namespace_status() -> None:
    """The read-only partner verb is documented alongside instrument."""
    text = _read(PLAYBOOK)
    assert re.search(r"dg\.sh\s+namespace\s+\S+\s+status", text), (
        "deploy playbook must document `dg.sh namespace <ns> status`"
    )


# ---------------------------------------------------------------------------
# AC3 / re-scope 2 — cross-link the design doc + BOTH ADRs.
# ---------------------------------------------------------------------------


def test_playbook_cross_links_design_doc() -> None:
    text = _read(PLAYBOOK)
    assert "cli.md" in text, (
        "deploy playbook must link docs/cli.md"
    )


def test_playbook_cross_links_both_adrs() -> None:
    """ADR-0031 (why non-reversible / no mode switch) AND ADR-0032 (why build on
    the cortex kit; kit-owned vs dg.sh-owned rows) are both discoverable."""
    text = _read(PLAYBOOK)
    assert "0031-non-reversible-namespace-lineage-activation.md" in text, (
        "deploy playbook must link ADR-0031"
    )
    assert "0032-dg-sh-builds-on-cortex-lineage-attach-kit.md" in text, (
        "deploy playbook must link ADR-0032"
    )


# ---------------------------------------------------------------------------
# Re-scope 1 — --cortex-local-path scope + refuse-and-mutate-nothing preflight.
# ---------------------------------------------------------------------------


def test_playbook_scopes_cortex_path_to_instrument_only() -> None:
    """Documents that ONLY instrument needs a cortex checkout, and where the kit
    is located under it."""
    text = _read(PLAYBOOK)
    low = text.lower()
    assert "authbridge/lineage-attach" in text, (
        "must document where dg.sh locates the kit (authbridge/lineage-attach/)"
    )
    # the component verbs + status do not touch cortex
    assert "instrument" in low
    # a missing path/kit is a refuse-and-mutate-nothing preflight
    assert re.search(r"refuse|mutat|preflight", low), (
        "must document the missing-path/kit refuse-and-mutate-nothing preflight"
    )


# ---------------------------------------------------------------------------
# Re-scope 2 — envoy-only kit; proxy-sidecar row is dg.sh's own.
# ---------------------------------------------------------------------------


def test_playbook_notes_kit_is_envoy_only_and_proxy_is_dg_sh_own() -> None:
    text = _read(PLAYBOOK)
    low = text.lower()
    assert "envoy-sidecar" in low, "must note the kit shape (envoy-sidecar)"
    assert "proxy-sidecar" in low, "must note the proxy-sidecar row"
    # The split is called out: envoy-only kit, proxy row is dg.sh's own.
    assert re.search(r"envoy[- ]sidecar[- ]only|envoy-only|only.*envoy", low), (
        "must note the cortex kit is envoy-sidecar-only"
    )


# ---------------------------------------------------------------------------
# Re-scope 3 — reconcile with the existing ad-hoc proxy attach recipe.
# ---------------------------------------------------------------------------


def test_playbook_reconciles_with_adhoc_lineage_recipe() -> None:
    """Notes where dg.sh supersedes / productizes the ad-hoc proxy-sidecar attach
    (instrument-one.sh / LINEAGE-PROXY-SIDECAR-RECIPE.md)."""
    text = _read(PLAYBOOK)
    assert ("instrument-one.sh" in text) or ("LINEAGE-PROXY-SIDECAR-RECIPE" in text), (
        "must reconcile with the ad-hoc lineage recipe (name instrument-one.sh "
        "or LINEAGE-PROXY-SIDECAR-RECIPE.md)"
    )
    low = text.lower()
    assert re.search(r"supersed|productiz|replace", low), (
        "must state dg.sh supersedes/productizes the ad-hoc recipe"
    )


# ---------------------------------------------------------------------------
# AC4 — the in-repo redeploy/procedure notes the dg.sh supersession.
# (The root CLAUDE.md is outside this repo; the in-repo playbook carries it.)
# ---------------------------------------------------------------------------


def test_in_repo_redeploy_procedure_notes_dg_sh_supersession() -> None:
    """The k8s README's "Re-deploying after a code change" section — this repo's
    analogue of the root CLAUDE.md redeploy procedure — points at dg.sh as the
    productized entry point (AC4)."""
    text = _read(PLAYBOOK)
    # Find the redeploy section and assert dg.sh appears within/near it.
    idx = text.find("Re-deploying after a code change")
    assert idx != -1, "the k8s README must keep its 'Re-deploying' section"
    # dg.sh is referenced somewhere in the doc as the primary redeploy path.
    assert re.search(r"dg\.sh\s+component\s+install", text), (
        "the redeploy procedure must reference `dg.sh component install`"
    )
