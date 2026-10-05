"""Exercise the cached-classification path through the real build script."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "build-and-load.sh"


def run_with_fake_images(tmp_path: Path, *, cached: bool) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "commands.log"
    scripts = {
        "podman": r'''printf 'podman %s\n' "$*" >> "$TRACE_LOG"
if [[ "$1 $2" == 'image inspect' && "$CACHE_IMAGE_PRESENT" == 0 ]]; then exit 1; fi
exit 0
''',
        "kind": r'''printf 'kind %s\n' "$*" >> "$TRACE_LOG"
exit 0
''',
        "uv": r'''printf 'uv %s\n' "$*" >> "$TRACE_LOG"
exit 0
''',
        "git": r'''printf 'git %s\n' "$*" >> "$TRACE_LOG"
printf 'dev\n'
exit 0
''',
    }
    for name, body in scripts.items():
        path = bin_dir / name
        path.write_text("#!/usr/bin/env bash\n" + body)
        path.chmod(0o755)
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "TRACE_LOG": str(log),
        "CACHE_IMAGE_PRESENT": "1" if cached else "0",
        "CONTAINER_TOOL": "podman",
        "KIND_CLUSTER": "rossoctl",
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), "--reuse-classification"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return result, log.read_text().splitlines() if log.exists() else []


def test_reuse_classification_builds_shared_image_but_only_loads_cached_model(
    tmp_path: Path,
) -> None:
    result, calls = run_with_fake_images(tmp_path, cached=True)

    assert result.returncode == 0, result.stderr
    builds = [call for call in calls if call.startswith("podman build ")]
    assert len(builds) == 1
    assert "Containerfile.classification" not in builds[0]
    assert not any(" lfs " in call for call in calls if call.startswith("git "))
    assert any("podman image inspect" in call and "classification:latest" in call for call in calls)
    assert any(
        "kind load docker-image docker.io/data-governance/classification:latest --name rossoctl" in call
        for call in calls
    )


def test_reuse_classification_fails_before_build_when_cache_is_missing(tmp_path: Path) -> None:
    result, calls = run_with_fake_images(tmp_path, cached=False)

    assert result.returncode != 0
    assert "classification" in result.stderr.lower()
    assert not any(call.startswith("podman build ") for call in calls)
