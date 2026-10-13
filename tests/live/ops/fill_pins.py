#!/usr/bin/env python3
"""Write tests/live/pins.<app>.local.yaml from what the build scripts made.

    usage: fill_pins.py travel_advisor|lineage_lab

Every value is read from the clones and the local image store (the commit of
each clone, `podman inspect` of each image, `ollama show` for the model), never
from the cluster: preflight then compares the cluster with this file, so a pod
running anything else is refused. The data-governance images are the ones
deploy/build-and-load.sh built (`docker.io/data-governance/receiver:latest`,
`docker.io/data-governance/classification:latest`). The file is gitignored and loaded in
preference to the committed template.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
APPS = {"travel_advisor": ("pins.yaml", "agent-examples-snp"),
        "lineage_lab": ("pins.lineage_lab.yaml", "agent-examples-snp-lab")}


def sh(*cmd: str) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()


def image_id(ref: str) -> str:
    try:
        return sh("podman", "inspect", "--format", "sha256:{{.Id}}", ref)
    except subprocess.CalledProcessError:
        sys.exit(f"image {ref} is not in the local store: build it first "
                 "(ops/build-sidecar.sh, ops/build-app.sh <app>, deploy/build-and-load.sh for data governance)")


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in APPS:
        sys.exit(__doc__)
    app = sys.argv[1]
    template, repo = APPS[app]
    snp, cortex = os.environ["E2E_AGENT_EXAMPLES_SNP"], os.environ["E2E_CORTEX_DIR"]
    snp_commit, cortex_commit = sh("git", "-C", snp, "rev-parse", "HEAD"), sh("git", "-C", cortex, "rev-parse", "HEAD")
    tag, ctag = snp_commit[:7], cortex_commit[:7]
    model = "qwen2.5:7b"
    try:
        modelfile = sh("ollama", "show", model, "--modelfile")
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        sys.exit(f"`ollama show {model} --modelfile` failed ({e}): the ollama CLI must be on PATH and the model pulled")
    digest = re.search(r"sha256[-:]([0-9a-f]{64})", modelfile)
    if not digest:
        sys.exit(f"no blob digest in `ollama show {model} --modelfile`")
    values = {
        ("agent_examples_snp", "commit"): snp_commit,
        ("agent_examples_snp", "image"): f"docker.io/library/{repo}:{tag}",
        ("agent_examples_snp", "image_id"): image_id(f"docker.io/library/{repo}:{tag}"),
        ("agent_examples_snp", "shim_image"): f"docker.io/library/{repo}-otel:{tag}",
        ("agent_examples_snp", "shim_image_id"): image_id(f"docker.io/library/{repo}-otel:{tag}"),
        ("cortex", "commit"): cortex_commit,
        ("cortex", "sidecar_image_id"): image_id(f"docker.io/library/authbridge-envoy:{ctag}"),
        ("cortex", "proxy_init_image_id"): image_id(f"docker.io/library/proxy-init:{ctag}"),
        ("dg", "receiver_image_id"): image_id("docker.io/data-governance/receiver:latest"),
        ("dg", "classification_image_id"): image_id("docker.io/data-governance/classification:latest"),
        ("llm", "digest"): f"sha256:{digest.group(1)}",
    }
    out, section, seen = [], None, set()
    for line in (HERE / template).read_text().splitlines():
        top = re.match(r"^(\w+):\s*$", line)
        if top:
            section = top.group(1)
        key = re.match(r"^(\s+)(\w+):\s", line)
        if key and (section, key.group(2)) in values:
            line = f"{key.group(1)}{key.group(2)}: {values[(section, key.group(2))]}"
            seen.add((section, key.group(2)))
        out.append(line)
    missing = set(values) - seen
    if missing:
        sys.exit(f"{template} has no line for {sorted(missing)}")
    target = HERE / f"pins.{app}.local.yaml"
    target.write_text("\n".join(out) + "\n")
    print(f"wrote {target.relative_to(HERE.parent.parent)}")
    for (s, k), v in values.items():
        print(f"  {s}.{k}: {v}")


if __name__ == "__main__":
    main()
