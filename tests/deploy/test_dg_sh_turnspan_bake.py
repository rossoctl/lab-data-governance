"""Structural tests for the two-shim ``-otel`` bake (#244, ADR-0033 Decision 4).

Issue #244 bakes **both** app-source-free shims into one ``-otel`` image:

  * the propagate-only activation hook (``lineage-propagate-hook.py`` →
    ``_lineage_propagate`` in site-packages, runs stock auto-instrumentation via
    ``initialize()`` when ``LINEAGE_PROPAGATE=1``), and
  * the **turn-span** shim (``rossoctl_turnspan.py`` + ``rossoctl_turnspan.pth``):
    a uvicorn ASGI middleware that opens one span per request scope seeded from
    the inbound W3C context, plus an MCP ``send_request`` / ``_handle_post_request``
    patch that re-attaches the turn context around the tool-call POST.

CRITICAL invariant (ADR-0033 "One trace needs two shims"): the turn-span shim
WITHOUT the propagate hook's ``initialize()`` leaves httpx instrumentation OFF and
produces **27** fragments — *worse* than the 11-fragment baseline. Because ONE
bake installs BOTH shims into the same image, that trap is structurally
unreachable. These tests pin the pieces that keep it so:

  1. both shim sources are vendored into ``deploy/lineage-attach/``;
  2. ``Dockerfile.otel-shim`` installs BOTH into the app's site-packages;
  3. ``build-otel-shim.sh``'s attestation asserts the turn-span module is
     importable (gate on), and its refuse-to-bake interlock catches an
     already-two-shim image so a re-bake cannot double-instrument.

These are static/structural assertions — no image is built. The behavioural
"dg.sh forwards the operator's cluster name into the bake" AC (the #241-review
cluster-name gap, folded into #244) is exercised against the real ``dg.sh`` in
``test_dg_sh_instrument.py``; the "the bake actually produces a one-trace image"
AC is the live wipe→instrument→demo→inspect run (ADR-0033 Consequences), out of
scope for a unit run.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
KIT = REPO_ROOT / "deploy" / "lineage-attach"

TURNSPAN_PY = KIT / "rossoctl_turnspan.py"
TURNSPAN_PTH = KIT / "rossoctl_turnspan.pth"
DOCKERFILE = KIT / "Dockerfile.otel-shim"
BUILD_SCRIPT = KIT / "build-otel-shim.sh"
ATTEST_SCRIPT = KIT / "attest-otel-shim.py"


# ---------------------------------------------------------------------------
# 1. The turn-span shim is vendored (a reviewed sibling of the propagate hook)
# ---------------------------------------------------------------------------


def test_turnspan_shim_is_vendored() -> None:
    """``rossoctl_turnspan.py`` + its ``.pth`` live in the vendored kit next to
    ``lineage-propagate-hook.py`` (ADR-0033 Decision 4: the turn-span shim gets a
    reviewed, tracked home in this repo, not the demo app)."""
    assert TURNSPAN_PY.is_file(), (
        f"turn-span shim must be vendored at {TURNSPAN_PY.relative_to(REPO_ROOT)}"
    )
    assert TURNSPAN_PTH.is_file(), (
        f"turn-span .pth must be vendored at {TURNSPAN_PTH.relative_to(REPO_ROOT)}"
    )


@pytest.fixture(scope="module")
def turnspan_text() -> str:
    return TURNSPAN_PY.read_text()


def test_turnspan_pth_activates_the_module_env_gated() -> None:
    """The ``.pth`` is the attach mechanism: ``site`` executes it at interpreter
    start and it must import + ``install()`` the module — env-gated on
    ``ROSSOCTL_TURNSPAN`` so an operator can switch it off (belt-and-suspenders;
    the module also guards internally)."""
    pth = TURNSPAN_PTH.read_text()
    assert "rossoctl_turnspan" in pth, ".pth must import the rossoctl_turnspan module"
    assert "install" in pth, ".pth must call the module's install() at startup"
    assert "ROSSOCTL_TURNSPAN" in pth, (
        ".pth must honour the ROSSOCTL_TURNSPAN opt-out so the turn span can be "
        "disabled without rebuilding the image"
    )


def test_turnspan_module_has_the_reviewed_surface(turnspan_text: str) -> None:
    """The vendored module is the reviewed artefact: it wraps one span per HTTP
    request scope (``TurnSpanMiddleware``), auto-inserts it by patching
    ``uvicorn.Config`` (``install``), and carries the turn's traceparent onto MCP
    tool-call POSTs (``_install_mcp_propagation``). Pin those three seams so a
    later hand-edit that guts the turn-span mechanism fails loudly."""
    for symbol in (
        "class TurnSpanMiddleware",
        "def install(",
        "def _install_mcp_propagation(",
    ):
        assert symbol in turnspan_text, (
            f"turn-span shim must define {symbol!r} (the reviewed mechanism)"
        )
    # It patches the two servers/clients the fleet actually uses.
    assert "uvicorn" in turnspan_text, "turn span auto-inserts via uvicorn.Config"
    assert "StreamableHTTPTransport" in turnspan_text, (
        "turn span must patch MCP's streamable-HTTP transport for tool-call POSTs"
    )


def test_turnspan_module_is_failsafe(turnspan_text: str) -> None:
    """A shim that rides inside the app must never take the app down. Every patch
    site is wrapped so a missing/renamed internal degrades to a transparent
    pass-through (propagation OFF, never a crash) — the same failure policy as the
    propagate hook."""
    assert turnspan_text.count("except Exception") >= 3, (
        "the uvicorn patch, the middleware's deferred OTel import, and the MCP "
        "patch must each be individually fail-safe"
    )


def test_turnspan_module_is_importable_with_only_the_stdlib(turnspan_text: str) -> None:
    """The module must import with NO OpenTelemetry / uvicorn / mcp present — its
    top level touches none of them (all deferred into ``install()`` / call time),
    so the ``.pth`` never breaks ``site`` on an app image that lacks them, and the
    build attestation can import it before those libs load."""
    r = _run_turnspan(turnspan_text, "import rossoctl_turnspan", env={})
    assert r.returncode == 0, (
        f"rossoctl_turnspan must import with only the stdlib present; "
        f"stderr:\n{r.stderr}"
    )


def test_turnspan_install_is_inert_when_activation_is_off(turnspan_text: str) -> None:
    """CRITICAL for build-otel-shim.sh's ``verify_inert`` attestation: with the
    activation switch OFF (``LINEAGE_PROPAGATE`` unset), ``install()`` must NOT
    import opentelemetry. ``install()`` patches MCP's transport, which imports
    ``opentelemetry`` — so on an app image that bundles ``mcp`` an unconditional
    ``install()`` would pull opentelemetry in at interpreter start and break the
    "gate off → nothing OTel loads" attestation. The turn span is bound to
    ``LINEAGE_PROPAGATE`` precisely so an unactivated image stays inert.

    (This test runs with mcp absent, which cannot itself trigger the import; it
    pins the LOGICAL guard — install() returns before its imports when
    unactivated — so a future edit that drops the ``_ACTIVATED`` gate is caught
    even in an mcp-free test environment.)"""
    prog = (
        "import sys, rossoctl_turnspan\n"
        "rossoctl_turnspan.install()\n"
        "otel = [m for m in sys.modules if m.startswith('opentelemetry')]\n"
        "assert not otel, 'gate off must not load opentelemetry: %r' % otel\n"
        # And it must not have patched uvicorn.Config (were uvicorn present).
        "print('OK')\n"
    )
    r = _run_turnspan(turnspan_text, prog, env={})  # LINEAGE_PROPAGATE unset
    assert r.returncode == 0, (
        f"install() must be inert (no opentelemetry import) when LINEAGE_PROPAGATE "
        f"is unset; stderr:\n{r.stderr}"
    )


def test_turnspan_install_is_bound_to_the_activation_switch(turnspan_text: str) -> None:
    """The turn span activates only when the propagate hook does. Assert the
    module reads ``LINEAGE_PROPAGATE`` and that ``install()`` short-circuits on it
    — the structural guarantee that the 27-fragment "turn span without activation"
    trap cannot occur even if the two ``.pth`` files ever diverged."""
    assert "LINEAGE_PROPAGATE" in turnspan_text, (
        "the turn span must bind to LINEAGE_PROPAGATE (the activation switch)"
    )
    # With activation ON but the opt-out set, install() is still a no-op (the
    # ROSSOCTL_TURNSPAN escape hatch), and still imports no opentelemetry.
    prog = (
        "import sys, rossoctl_turnspan\n"
        "rossoctl_turnspan.install()\n"
        "otel = [m for m in sys.modules if m.startswith('opentelemetry')]\n"
        "assert not otel, otel\n"
        "print('OK')\n"
    )
    r = _run_turnspan(
        turnspan_text, prog, env={"LINEAGE_PROPAGATE": "1", "ROSSOCTL_TURNSPAN": "off"}
    )
    assert r.returncode == 0, (
        f"ROSSOCTL_TURNSPAN=off must keep install() a no-op even when activated; "
        f"stderr:\n{r.stderr}"
    )


def _run_turnspan(turnspan_text: str, prog: str, env: dict) -> subprocess.CompletedProcess:
    """Run ``prog`` in a bare interpreter with the vendored turn-span module on
    PYTHONPATH and ONLY the given env (plus PATH) — no ambient LINEAGE_PROPAGATE /
    ROSSOCTL_TURNSPAN leaks in."""
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "rossoctl_turnspan.py").write_text(turnspan_text)
        run_env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": d}
        run_env.update(env)
        return subprocess.run(
            [sys.executable, "-c", prog],
            cwd=d,
            capture_output=True,
            text=True,
            env=run_env,
        )


# ---------------------------------------------------------------------------
# 2. Dockerfile.otel-shim installs BOTH shims into the app's site-packages
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dockerfile_text() -> str:
    return DOCKERFILE.read_text()


def test_dockerfile_still_installs_the_propagate_hook(dockerfile_text: str) -> None:
    """Regression guard: extending the bake must NOT drop the activation hook —
    the turn span WITHOUT it is worse than baseline (27 fragments)."""
    assert "lineage-propagate-hook.py" in dockerfile_text, (
        "Dockerfile must still COPY the propagate/activation hook"
    )
    assert "_lineage_propagate.py" in dockerfile_text, (
        "Dockerfile must still install the hook as _lineage_propagate.py"
    )


def test_dockerfile_installs_the_turnspan_shim(dockerfile_text: str) -> None:
    """ADR-0033 Decision 4: the same Dockerfile installs the turn-span shim into
    the app's site-packages too — one bake, one image carrying activation AND the
    turn span."""
    assert "rossoctl_turnspan.py" in dockerfile_text, (
        "Dockerfile must COPY rossoctl_turnspan.py into the image"
    )
    assert "rossoctl_turnspan.pth" in dockerfile_text, (
        "Dockerfile must install the rossoctl_turnspan .pth so `site` activates "
        "the turn span at interpreter start"
    )


def test_dockerfile_lands_turnspan_in_the_resolved_site_packages(
    dockerfile_text: str,
) -> None:
    """Both shims must land in the SAME environment the app runs in. The hook is
    installed into the interpreter's ``purelib`` (``${sp}``); the turn span must
    ride the same ``purelib`` resolution, not a hard-coded path that could miss a
    non-standard venv layout."""
    assert "purelib" in dockerfile_text, (
        "Dockerfile must resolve site-packages via sysconfig purelib for BOTH "
        "shims (so a non-standard venv layout still gets them)"
    )
    assert not re.search(r"/usr/lib/python[0-9.]+/site-packages", dockerfile_text), (
        "shims must not be installed at a hard-coded /usr/lib site-packages path; "
        "resolve the app interpreter's purelib instead"
    )


def test_dockerfile_installs_turnspan_before_dropping_privileges(
    dockerfile_text: str,
) -> None:
    """The final ``USER ${APP_UID}:${APP_GID}`` drops root; every shim install
    (which writes into site-packages) must happen BEFORE it, or the COPY/install
    fails on a read-only-to-the-app site-packages."""
    lines = dockerfile_text.splitlines()
    user_idx = max(
        (i for i, ln in enumerate(lines) if re.match(r"^\s*USER\s+\$\{?APP_UID", ln)),
        default=-1,
    )
    assert user_idx >= 0, "Dockerfile must reset USER to the detected app uid:gid"
    turnspan_idx = max(
        (i for i, ln in enumerate(lines) if "rossoctl_turnspan" in ln),
        default=-1,
    )
    assert turnspan_idx >= 0, "Dockerfile must reference the turn-span shim"
    assert turnspan_idx < user_idx, (
        "the turn-span shim must be installed while still root (before the final "
        f"USER drop at line {user_idx + 1}); last turnspan ref at line {turnspan_idx + 1}"
    )


# ---------------------------------------------------------------------------
# 3. build-otel-shim.sh: attestation asserts the turn span, interlock catches
#    an already-two-shim image
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def build_script_text() -> str:
    return BUILD_SCRIPT.read_text()


@pytest.fixture(scope="module")
def attest_script_text() -> str:
    return ATTEST_SCRIPT.read_text()


def test_build_script_attestation_asserts_turnspan_importable(
    build_script_text: str, attest_script_text: str,
) -> None:
    """ADR-0033 Decision 4: the bake's attestation is extended to assert the
    turn-span module is importable — so a bake that silently failed to install it
    (a rename, a COPY drop) fails the BUILD, not the cluster. The assertion lives
    in the gate-ON attestation (``verify_propagates``), which runs the interpreter
    with the shim environment up."""
    assert "attest-otel-shim.py" in build_script_text
    imports_turnspan = bool(
        re.search(r"import\s+rossoctl_turnspan", attest_script_text)
        or re.search(r"find_spec\(\s*[\"']rossoctl_turnspan", attest_script_text)
        or re.search(r"__import__\(\s*[\"']rossoctl_turnspan", attest_script_text)
    )
    assert imports_turnspan, (
        "the attestation must actually import (or find_spec) rossoctl_turnspan so "
        "a missing turn-span shim fails the bake"
    )


def test_build_script_exposes_existing_image_attestation(
    build_script_text: str,
) -> None:
    """Reruns prove image contents; an ``-otel`` suffix is never provenance."""
    assert "--attest-existing" in build_script_text
    attest_branch = build_script_text[build_script_text.index('"--attest-existing"') :]
    assert "verify_inert" in attest_branch
    assert "verify_propagates" in attest_branch


def test_build_script_interlock_detects_the_turnspan_shim(
    build_script_text: str,
) -> None:
    """The refuse-to-bake interlock must recognise an ALREADY-two-shim ``-otel``
    image and refuse to re-bake it (double-instrument). It already detects the
    ``_lineage_propagate`` hook; it must also see ``rossoctl_turnspan`` so a
    turn-span-only image (were the hook probe ever loosened) is still caught.
    Scope the check to the probe function so a stray mention elsewhere cannot
    satisfy it."""
    m = re.search(
        r"probe_instrumentation\(\)\s*\{.*?\n\}", build_script_text, flags=re.DOTALL
    )
    assert m, "build-otel-shim.sh must have a probe_instrumentation() function"
    probe = m.group(0)
    assert "rossoctl_turnspan" in probe, (
        "the bake interlock probe must detect an already-installed rossoctl_turnspan "
        "shim so re-baking an -otel image is refused (double-instrument)"
    )
