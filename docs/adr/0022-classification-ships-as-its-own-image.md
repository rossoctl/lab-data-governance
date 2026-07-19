# P-classification ships as its own container image, not the shared one

The new `P-classification` processor runs a fine-tuned NER model (a ~500 MB
`RobertaForTokenClassification` git-LFS artifact) on top of `torch` +
`transformers`. It ships as its own image, `data-governance/classification`,
built from a dedicated `Containerfile.classification` — **separate** from the
single shared image that the receiver, UI, and interactions Deployments all run
(issue #38). `torch`/`transformers` live behind a `classification`
optional-dependency extra in `pyproject.toml` so only this image installs them;
the shared image's `uv sync --no-dev` (no extra) stays slim. The model weights
and the two classification config artifacts are baked into the image
(see ADR-0023).

## Why

- **The dependency closure diverges heavily, unlike the other three services.**
  Issue #38's single-image contract is a deliberate, tested choice: one
  Containerfile, one image, receiver/UI/interactions differ only by `command:`.
  Its accepted cost is a few hundred KB of unused React assets on the
  receiver/interactions pods — genuinely negligible. `torch` (~1–2 GB) plus
  ~500 MB of weights is not: it would tax three services that never touch the
  model with a ~2 GB penalty each. The rule that keeps issue #38 sound —
  *share an image across services with the same dependency closure* — is
  exactly the rule that says split when a service's closure blows up.
- **Splitting only classification is principled; splitting all four would be
  cargo-culting.** Receiver/UI/interactions share a closure and gain almost
  nothing from separation (the venv is identical — one `pyproject.toml`);
  keeping them together stays trivially reversible. Classification is the sole
  divergent consumer, so it is the sole split.

## Considered alternatives

- **Add `torch` + weights to the shared image (honor issue #38 literally).**
  Rejected: ~2 GB of ML stack on the receiver, UI, and interactions pods for
  something none of them run.
- **Split every service into its own image for symmetry.** Rejected: reverses
  issue #38 for no dependency reason, and turns one build/load into four with
  four images to keep in sync. Symmetry is not a forcing function.
- **`FROM data-governance/receiver:latest`, then `pip install torch` on top.**
  Rejected: couples build ordering (base first) and layers the ML stack outside
  the uv/lockfile-exact discipline the base Containerfile is built around. A
  standalone multi-stage `Containerfile.classification` sharing the one
  `pyproject.toml`/`uv.lock` keeps that discipline.

## Consequences

- `build-and-load.sh` grows a second build+load for
  `data-governance/classification:latest`.
- `tests/deploy/test_manifests.py`'s "every Deployment runs
  `data-governance/receiver:latest`" invariant no longer holds for the
  classification Deployment; the assertion is scoped to the three
  shared-image services.
- The split stays narrow by design: if `interactions` ever grows its own heavy
  divergent deps, it is split *then*, on its own merits — not now, for symmetry.
