"""Structural assertions for the v1 container image build (issue #38).

The v1 manifests in ``deploy/k8s/`` reference ``data-governance/receiver:latest``
and ``data-governance/ui:latest`` with ``imagePullPolicy: IfNotPresent``. Issue
#38 adds the **single** Containerfile that produces the image both Deployments
need plus build-and-load tooling that tags the same image twice and ``kind
load``s it into the local Kind cluster named ``kagenti``.

These tests pin the structural contract — a hand-edit that drops
``uv sync --frozen``, omits the migrations tree, switches to ``pip install``,
or stops tagging both image tags should fail loudly. They do NOT actually run
``docker build`` or ``kind load``; the "image actually builds and pods reach
Ready" acceptance criterion is exercised manually as documented in
``deploy/k8s/README.md``.
"""

from __future__ import annotations

import os
import re
import stat
import tomllib
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Containerfile (single file at repo root)
# ---------------------------------------------------------------------------


def _find_containerfile() -> Path:
    """Return the single Containerfile or Dockerfile at the repo root.

    Issue #38 explicitly mandates ONE file. Either name is acceptable
    (Containerfile is the OCI-neutral spelling, Dockerfile the legacy one).
    """
    candidates = [REPO_ROOT / "Containerfile", REPO_ROOT / "Dockerfile"]
    present = [p for p in candidates if p.is_file()]
    assert present, (
        "expected a single Containerfile or Dockerfile at the repo root; "
        f"checked {[str(p) for p in candidates]}"
    )
    assert len(present) == 1, (
        f"expected exactly one of Containerfile or Dockerfile at the repo "
        f"root, found both: {[str(p) for p in present]}"
    )
    return present[0]


@pytest.fixture(scope="module")
def containerfile_text() -> str:
    return _find_containerfile().read_text()


def test_containerfile_present_at_repo_root() -> None:
    _find_containerfile()  # raises if missing or duplicated


def test_containerfile_is_multi_stage(containerfile_text: str) -> None:
    """Issue #38 pins a multi-stage build: a builder stage that runs
    ``uv sync --frozen`` and a thin runtime stage. Two ``FROM`` lines are the
    minimum the contract requires.
    """
    from_lines = [
        line
        for line in containerfile_text.splitlines()
        if re.match(r"^\s*FROM\s+", line, flags=re.IGNORECASE)
    ]
    assert len(from_lines) >= 2, (
        f"Containerfile must be multi-stage (>=2 FROM lines), got {from_lines!r}"
    )


def test_containerfile_uses_uv_sync_frozen(containerfile_text: str) -> None:
    """The builder stage installs deps from ``uv.lock`` via ``uv sync --frozen``.

    Why ``--frozen``: issue #38 calls for a deterministic environment from
    ``pyproject.toml + uv.lock``, not re-resolution at build time. Plain
    ``uv sync`` (or any ``uv lock --upgrade`` invocation) drifts off the
    lockfile.
    """
    assert "uv sync --frozen" in containerfile_text, (
        "Containerfile must invoke `uv sync --frozen` so the image's "
        "environment matches uv.lock; got Containerfile without that "
        "literal string"
    )


def test_containerfile_does_not_use_pip_install_of_project(
    containerfile_text: str,
) -> None:
    """The build is uv-native (project README + #38). A ``pip install`` of the
    project itself would defeat the lockfile contract.
    """
    pip_install_lines = [
        line
        for line in containerfile_text.splitlines()
        if re.search(r"\bpip\s+install\b", line)
    ]
    # Allow no pip install lines at all. (Bootstrapping uv via `pip install
    # uv` is also unnecessary — the official ghcr.io/astral-sh/uv image ships
    # uv preinstalled. If a future contributor needs it, the test can be
    # relaxed; for now it is the simplest, sharpest signal.)
    assert not pip_install_lines, (
        "Containerfile must not use `pip install` — the build is uv-native "
        f"per issue #38; got: {pip_install_lines!r}"
    )


def test_containerfile_copies_alembic_ini(containerfile_text: str) -> None:
    """Issue #38 acceptance criterion: the image must contain ``alembic.ini``
    so ``python -m data_governance.db.migrate`` and the schema-version
    startup check resolve correctly."""
    assert "alembic.ini" in containerfile_text, (
        "Containerfile must COPY alembic.ini into the runtime image"
    )


def test_containerfile_includes_data_governance_package(
    containerfile_text: str,
) -> None:
    """The migrations tree lives at ``data_governance/db/migrations/``; copying
    the ``data_governance`` package directory pulls it in along with the rest
    of the source tree the runtime needs.
    """
    # Either `COPY data_governance` (full directory copy) or a wildcard like
    # `COPY . .` is acceptable — both pull in the migrations tree. The check
    # is "the image will end up with data_governance importable."
    has_pkg_copy = (
        "data_governance" in containerfile_text
        or re.search(r"^\s*COPY\s+\.\s+", containerfile_text, flags=re.MULTILINE)
    )
    assert has_pkg_copy, (
        "Containerfile must COPY the data_governance package (or the whole "
        "repo) so migrations and source are present in the runtime image"
    )


def test_containerfile_does_not_pin_command_to_one_entrypoint(
    containerfile_text: str,
) -> None:
    """One image, two tags, two entry points: the receiver Deployment sets
    ``command: ["python", "-m", "data_governance.processors.otlp_receiver"]``
    and the UI Deployment sets ``command: ["python", "-m",
    "data_governance.api"]``. The image's default ``CMD`` (if any) must not
    *prevent* either by hardcoding incompatible flags.

    The tightest assertion we can make at static-analysis time is: there is
    no ``ENTRYPOINT`` that wraps a single Python module — that would force
    every k8s ``command:`` override to fight the entrypoint. A bare
    ``CMD ["python", ...]`` with a default is fine because k8s ``command:``
    overrides ``ENTRYPOINT`` and ``CMD`` together.
    """
    entrypoint_lines = [
        line
        for line in containerfile_text.splitlines()
        if re.match(r"^\s*ENTRYPOINT\s+", line, flags=re.IGNORECASE)
    ]
    for line in entrypoint_lines:
        # An ENTRYPOINT that pins a specific module would lock the image to
        # one role; reject the receiver/api module references inside
        # ENTRYPOINT specifically.
        assert "data_governance.processors.otlp_receiver" not in line, (
            f"ENTRYPOINT must not pin the receiver module; got {line!r}"
        )
        assert "data_governance.api" not in line, (
            f"ENTRYPOINT must not pin the api module; got {line!r}"
        )


# ---------------------------------------------------------------------------
# pyproject: torch/transformers live behind the `classification` extra (issue #79)
# ---------------------------------------------------------------------------
#
# ADR-0022: only the classification image installs the ~2 GB torch stack. It must
# be an OPTIONAL dependency (an extra), never a core dependency — otherwise the
# shared receiver/UI/interactions image's `uv sync` (which installs no extra)
# would drag torch in and blow the slim-image contract. These tests pin that the
# extra exists and carries torch + transformers, and that the core dependency
# list carries neither.


@pytest.fixture(scope="module")
def pyproject() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def test_classification_extra_carries_torch_and_transformers(pyproject: dict) -> None:
    """A ``classification`` optional-dependency extra bundles the NER model's
    runtime (torch + transformers), so only the classification image installs
    them (ADR-0022)."""
    extras = pyproject["project"].get("optional-dependencies", {})
    assert "classification" in extras, (
        "pyproject must declare a `classification` optional-dependency extra "
        f"(got extras: {sorted(extras)})"
    )
    packages = " ".join(extras["classification"]).lower()
    assert "torch" in packages, "classification extra must include torch"
    assert "transformers" in packages, "classification extra must include transformers"


def test_core_dependencies_are_torch_free(pyproject: dict) -> None:
    """The shared image installs only core deps (`uv sync --no-dev`, no extra), so
    torch/transformers must NOT appear in `[project.dependencies]` — otherwise the
    receiver/UI/interactions image would carry the ML stack it never runs
    (ADR-0022)."""
    core = " ".join(pyproject["project"].get("dependencies", [])).lower()
    assert "torch" not in core, "torch must not be a core dependency (ADR-0022)"
    assert "transformers" not in core, (
        "transformers must not be a core dependency (ADR-0022)"
    )


# ---------------------------------------------------------------------------
# Containerfile.classification — the separate P-classification image (issue #79)
# ---------------------------------------------------------------------------
#
# ADR-0022/0023: P-classification ships as its own image
# (`data-governance/classification`), separate from the shared receiver/UI/
# interactions image, because its torch + ~500 MB model-weight closure diverges
# heavily. The dedicated `Containerfile.classification` installs the
# `classification` extra (torch/transformers) and bakes in — as one unit — the
# model weights AND both config artifacts (image tag ↔ model_version). These tests
# pin that structural contract statically; the "image actually builds and the model
# loads" AC is exercised via build-and-load.sh on the cluster (git-LFS weights are
# not materialized in a fresh dev checkout), exactly as the shared image's build is.

CLASSIFICATION_CONTAINERFILE = REPO_ROOT / "Containerfile.classification"


def test_classification_containerfile_present() -> None:
    """A dedicated Containerfile.classification exists at the repo root (ADR-0022:
    classification is the sole split-off image)."""
    assert CLASSIFICATION_CONTAINERFILE.is_file(), (
        "expected Containerfile.classification at the repo root for the separate "
        "P-classification image (ADR-0022)"
    )


@pytest.fixture(scope="module")
def classification_containerfile_text() -> str:
    return CLASSIFICATION_CONTAINERFILE.read_text()


def test_classification_containerfile_installs_the_extra(
    classification_containerfile_text: str,
) -> None:
    """The classification image installs the `classification` extra (torch +
    transformers) via uv — that is what makes it the "fat" image (ADR-0022). A
    plain `uv sync` with no extra would omit the model runtime."""
    text = classification_containerfile_text
    assert "uv sync --frozen" in text, (
        "classification image must install deps via `uv sync --frozen` "
        "(lockfile-exact, like the shared image)"
    )
    assert "--extra classification" in text, (
        "classification image must `uv sync ... --extra classification` so torch/"
        "transformers are installed (ADR-0022)"
    )


def test_classification_containerfile_bakes_weights_and_config(
    classification_containerfile_text: str,
) -> None:
    """ADR-0023: the ~500 MB model weights AND both config artifacts are baked into
    the image as one unit (image tag ↔ model_version). The Containerfile must copy
    the model directory (weights + config.json) into the image; the config
    artifacts (classification_config.json + entity CSV) travel with the package
    already, so the model weights are the piece the image adds."""
    text = classification_containerfile_text
    # The model generation directory holds config.json + model.safetensors.
    assert "classification/model" in text, (
        "classification image must COPY the model weights directory "
        "(classification/model/<generation>) into the image (ADR-0023)"
    )


def test_classification_containerfile_points_model_dir_at_the_baked_weights(
    classification_containerfile_text: str,
) -> None:
    """The processor resolves the model from ``CLASSIFICATION_MODEL_DIR`` (default
    ``/app/model``). The image must put the weights where the processor looks —
    either by copying to ``/app/model`` or by setting ``CLASSIFICATION_MODEL_DIR``
    to wherever it copied them — so startup finds the baked-in model (ADR-0023, no
    runtime fetch)."""
    text = classification_containerfile_text
    copies_to_default = "/app/model" in text
    sets_model_dir_env = re.search(
        r"ENV\s+.*CLASSIFICATION_MODEL_DIR", text
    ) or "CLASSIFICATION_MODEL_DIR" in text
    assert copies_to_default or sets_model_dir_env, (
        "classification image must bake the weights where the processor resolves "
        "the model: copy them to /app/model (the default) or set "
        "CLASSIFICATION_MODEL_DIR to their baked location"
    )


def test_classification_containerfile_runs_the_classification_entrypoint(
    classification_containerfile_text: str,
) -> None:
    """The image's default command (if any) must not pin an entrypoint that fights
    the k8s `command:` override; and it should be capable of running the
    classification processor. We assert the classification module is referenced so
    the image is self-evidently the P-classification image, and that no ENTRYPOINT
    hardcodes a different module."""
    text = classification_containerfile_text
    assert "data_governance.processors.classification" in text, (
        "classification image should reference the classification entry point"
    )
    entrypoint_lines = [
        line
        for line in text.splitlines()
        if re.match(r"^\s*ENTRYPOINT\s+", line, flags=re.IGNORECASE)
    ]
    for line in entrypoint_lines:
        assert "data_governance.processors.otlp_receiver" not in line
        assert "data_governance.api" not in line


def test_classification_containerfile_is_multi_stage(
    classification_containerfile_text: str,
) -> None:
    """Mirror the shared image's multi-stage discipline: a uv builder stage and a
    slim runtime stage (>=2 FROM lines)."""
    from_lines = [
        line
        for line in classification_containerfile_text.splitlines()
        if re.match(r"^\s*FROM\s+", line, flags=re.IGNORECASE)
    ]
    assert len(from_lines) >= 2, (
        f"Containerfile.classification must be multi-stage (>=2 FROM), got {from_lines!r}"
    )


# ---------------------------------------------------------------------------
# Container ignore file (deterministic build inputs)
# ---------------------------------------------------------------------------


def _find_container_ignore() -> Path:
    """Return the single ``.containerignore`` or ``.dockerignore`` at the
    repo root.

    Issue #38 mandates deterministic builds. Without an ignore file the
    build context varies with whatever ``__pycache__/``, ``.pyc``, or
    ``.venv/`` files happen to be in the contributor's checkout, and
    combined with ``UV_COMPILE_BYTECODE=1`` this means two "clean"
    checkouts produce different images. Both docker and podman honour
    either filename; the project picks one and is consistent.
    """
    candidates = [REPO_ROOT / ".containerignore", REPO_ROOT / ".dockerignore"]
    present = [p for p in candidates if p.is_file()]
    assert present, (
        "expected a .containerignore or .dockerignore at the repo root so "
        f"the build context is deterministic; checked {[str(p) for p in candidates]}"
    )
    assert len(present) == 1, (
        "expected exactly one of .containerignore or .dockerignore at the "
        f"repo root, found both: {[str(p) for p in present]}"
    )
    return present[0]


@pytest.fixture(scope="module")
def container_ignore_text() -> str:
    return _find_container_ignore().read_text()


def test_container_ignore_present_at_repo_root() -> None:
    _find_container_ignore()  # raises if missing or duplicated


@pytest.mark.parametrize(
    "pattern",
    [
        "__pycache__",
        "*.pyc",
        "*.pyo",
        ".venv",
        ".pytest_cache",
        "*.egg-info",
        ".git",
        "tests",
    ],
)
def test_container_ignore_excludes_required_pattern(
    container_ignore_text: str, pattern: str
) -> None:
    """Every required exclude must appear as its own (possibly anchored or
    suffixed) line in the ignore file.

    Why these specific patterns: Python build artefacts (``__pycache__/``,
    ``*.pyc``, ``*.pyo``, ``*.egg-info/``) and developer-local state
    (``.venv/``, ``.pytest_cache/``, ``.git/``) vary across checkouts and
    feed straight into the build context if not excluded. ``tests/`` is
    not needed by the runtime image (the Containerfile does not run
    pytest) and bloats the build context.
    """
    # Normalise: split into lines, strip whitespace and a leading '/' (some
    # styles anchor patterns at the context root with `/foo`). Trailing
    # slashes are also conventional for directory-only patterns; accept
    # either form.
    lines = {
        stripped.lstrip("/").rstrip("/")
        for line in container_ignore_text.splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    }
    assert pattern in lines, (
        f"`.containerignore` (or `.dockerignore`) must exclude {pattern!r} "
        f"so the build context is deterministic across contributors; "
        f"got non-comment entries: {sorted(lines)!r}"
    )


# ---------------------------------------------------------------------------
# Build + Kind-load script
# ---------------------------------------------------------------------------


SCRIPT_PATH = REPO_ROOT / "deploy" / "build-and-load.sh"


def test_build_load_script_exists() -> None:
    """A shell script under ``deploy/`` builds the image and ``kind load``s it.

    Issue #38 accepts either a Make target, a uv script, or a shell script.
    We standardise on a shell script under ``deploy/`` because (a) the rest of
    the repo has no Makefile, (b) shell is the lowest-dep choice, and (c)
    grouping deployment tooling under ``deploy/`` matches the existing layout
    (``deploy/k8s/``).
    """
    assert SCRIPT_PATH.is_file(), (
        f"expected build + load script at {SCRIPT_PATH.relative_to(REPO_ROOT)}"
    )


def test_build_load_script_is_executable() -> None:
    """``./deploy/build-and-load.sh`` should be runnable directly."""
    mode = SCRIPT_PATH.stat().st_mode
    assert mode & stat.S_IXUSR, (
        f"{SCRIPT_PATH.relative_to(REPO_ROOT)} must be executable "
        f"(run chmod +x); got mode {oct(mode)}"
    )


@pytest.fixture(scope="module")
def script_text() -> str:
    return SCRIPT_PATH.read_text()


def test_build_load_script_tags_both_images(script_text: str) -> None:
    """The single image must end up tagged both as the receiver and the UI
    image, matching the manifests' references.
    """
    assert "data-governance/receiver:latest" in script_text, (
        "build script must tag the image as data-governance/receiver:latest"
    )
    assert "data-governance/ui:latest" in script_text, (
        "build script must tag the image as data-governance/ui:latest"
    )


def test_build_load_script_loads_into_kind_kagenti(script_text: str) -> None:
    """The script ``kind load docker-image``s both tags into the cluster
    named ``kagenti`` (the upstream Kagenti convention).

    The cluster name may be provided literally on the ``kind load`` line, or
    expanded from a shell variable whose default is ``kagenti``. Either is
    acceptable; what we assert is that ``kagenti`` is the default target so
    a developer running the script with no env vars hits the right cluster.
    """
    assert "kind load docker-image" in script_text, (
        "build script must invoke `kind load docker-image`"
    )
    # The script must drive `kind load` via a `--name` flag (literal or
    # variable). A bare `kind load docker-image IMG` would target whatever
    # cluster `kind` defaults to ("kind"), not kagenti.
    assert re.search(r"--name[\s=]", script_text), (
        "build script must pass --name to `kind load docker-image` so it "
        "targets a specific cluster"
    )
    # And `kagenti` must be the default cluster name. Accept either a literal
    # `--name kagenti` (no variable) or a `${KIND_CLUSTER:-kagenti}`-style
    # default.
    has_literal_kagenti = bool(
        re.search(r"--name[\s=]+[\"']?kagenti[\"']?\b", script_text)
    )
    has_default_kagenti = bool(
        re.search(r":-\s*kagenti\b", script_text)
    )
    assert has_literal_kagenti or has_default_kagenti, (
        "build script must default to the `kagenti` Kind cluster "
        "(literal `--name kagenti` or `${KIND_CLUSTER:-kagenti}`)"
    )


def test_build_load_script_builds_the_classification_image(script_text: str) -> None:
    """Issue #79/ADR-0022: the script also builds the SECOND image
    `data-governance/classification:latest` from `Containerfile.classification`
    (torch + baked weights), distinct from the shared receiver/UI image."""
    assert "Containerfile.classification" in script_text, (
        "build script must build from Containerfile.classification (the separate "
        "P-classification image; ADR-0022)"
    )
    assert "data-governance/classification:latest" in script_text, (
        "build script must produce data-governance/classification:latest"
    )


def test_build_load_script_loads_the_classification_image_into_kind(
    script_text: str,
) -> None:
    """The classification image is `kind load`ed into the kagenti cluster too, so
    the classification Deployment's IfNotPresent tag resolves on a fresh cluster —
    like the shared image."""
    # There must be a kind-load referencing the classification image (literal or
    # via a variable that expands to it). We assert both the classification image
    # name and a `kind load docker-image` invocation are present; the shared-image
    # test already pins the --name kagenti default that this line shares.
    assert "data-governance/classification" in script_text
    assert script_text.count("kind load docker-image") >= 2, (
        "build script must `kind load docker-image` BOTH the shared image and the "
        "classification image (>=2 loads)"
    )


def test_build_load_script_materializes_git_lfs_before_build(script_text: str) -> None:
    """ADR-0023: the ~500 MB model weights are a git-LFS artifact that must be
    MATERIALIZED (not left as a pointer) before the classification build, or the
    image would bake a 134-byte pointer instead of the weights. The script must
    run `git lfs` (pull/fetch/checkout) before building the classification image."""
    assert re.search(r"git\s+lfs\b", script_text), (
        "build script must materialize git-LFS (e.g. `git lfs pull`) before the "
        "classification build so the real weights are baked, not the LFS pointer "
        "(ADR-0023)"
    )


def test_build_load_script_refreshes_uv_lock_before_build(script_text: str) -> None:
    """Both Containerfiles run `uv sync --frozen`, which aborts if `uv.lock` is
    missing or out of sync with `pyproject.toml`. `uv.lock` is .gitignored, so a
    checkout carries whatever (possibly stale, possibly absent) lock the host last
    generated — and issue #79 added torch/transformers to the `classification`
    extra, so a pre-#79 lock lacks them and `uv sync --frozen --extra classification`
    would fail the build. The script must `uv lock` (idempotent) before the build so
    the `--frozen` step resolves against a current lockfile rather than depending on
    the host having manually re-locked."""
    assert re.search(r"uv\s+lock\b", script_text), (
        "build script must run `uv lock` before the `uv sync --frozen` builds so "
        "the (gitignored) lockfile matches pyproject.toml — otherwise a stale/absent "
        "lock (e.g. one predating issue #79's torch/transformers extra) fails the "
        "classification image build"
    )


def test_build_load_script_uses_set_e_or_pipefail(script_text: str) -> None:
    """A multi-step shell script that builds, tags, and loads must abort on
    the first failure — otherwise a failed build silently proceeds to a
    ``kind load`` of a stale image, producing the very ErrImagePull /
    crashloop the issue is trying to eliminate.
    """
    # Either `set -e` on its own or as part of `set -euo pipefail` is fine.
    has_set_e = bool(
        re.search(r"^\s*set\s+-[a-zA-Z]*e[a-zA-Z]*", script_text, flags=re.MULTILINE)
    )
    assert has_set_e, (
        "build script must `set -e` (typically `set -euo pipefail`) so a "
        "failed build does not silently proceed to kind load"
    )


# ---------------------------------------------------------------------------
# Documentation: build + load is a prerequisite for `kubectl apply`
# ---------------------------------------------------------------------------


def test_deploy_readme_documents_build_and_load() -> None:
    """``deploy/k8s/README.md`` (or a sibling ``deploy/README.md``) must
    document the build + load flow as the prerequisite to ``kubectl apply``.

    Issue #38 acceptance criterion: a developer reading the deploy docs must
    see "build the image and kind load it before kubectl apply" — otherwise
    the manifests' ``IfNotPresent`` tags will ErrImagePull on a fresh cluster.
    """
    candidates = [REPO_ROOT / "deploy" / "k8s" / "README.md", REPO_ROOT / "deploy" / "README.md"]
    docs = [p for p in candidates if p.is_file()]
    assert docs, "expected deploy/k8s/README.md or deploy/README.md to exist"

    combined = "\n\n".join(p.read_text() for p in docs)
    assert "build-and-load.sh" in combined or "build-and-load" in combined, (
        "deploy README must reference the build-and-load script"
    )
    # The flow must mention `kind load` (or the script that wraps it) and
    # frame it as preceding `kubectl apply`. We assert the two literals are
    # present in the same document; ordering is harder to pin reliably and
    # less load-bearing than presence.
    assert "kind load" in combined or "build-and-load.sh" in combined, (
        "deploy README must explain how the image gets into Kind"
    )
    assert "kubectl apply" in combined, (
        "deploy README must reference `kubectl apply` so the prerequisite "
        "ordering is visible"
    )
