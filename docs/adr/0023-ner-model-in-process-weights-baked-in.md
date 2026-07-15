# P-classification runs the NER model in-process, with weights and config baked into the image

`P-classification` loads the fine-tuned NER model at startup and runs inference
**in-process**, inline in its drain loop — it does not call out to a separate
inference service. The model weights (~500 MB git-LFS artifact) and both
classification config artifacts — `classification_config.json` (identity-bundle
patterns + hierarchy) and the `EntityTypesForTokenClassification_with_tags.csv`
entity metadata — are **baked into the classification image** at build time
(`build-and-load.sh` materializes git-LFS before the build). The image tag is
the model generation: image tag ↔ the `model_version` stamped on every
`payload_classifications` row.

## Why

- **In-process matches the established processor shape and fits the drain
  loop.** `P-interactions` is one self-contained Deployment; a separate
  inference service would invent an HTTP contract and a "model unavailable"
  failure mode on day one. The shared driver (ADR-0007, and the extracted
  `processors/_driver.py`) already processes one item per transaction, so
  synchronous inference fits: a slow payload only slows the cursor, and a crash
  mid-inference re-processes from the same durable cursor (idempotent —
  ADR-0007). The image is the "fat" one by design (ADR-0022), so torch +
  weights in it is in character.
- **Baking weights + config as one unit prevents model/CSV tag drift.** The
  CSV's tag set and the model's `id2label` must agree; a tag the model emits
  but the CSV lacks silently defaults to `INTERNAL` (the `--validate-tags` flag
  exists precisely because this drift is a known hazard). Shipping weights, CSV,
  and JSON together in one image means one `model_version` bump moves all three
  as a unit. A retrain is a full image rebuild + reload — the same cadence the
  truncate-and-reclassify upgrade runbook already assumes (ADR-0024).
- **Self-contained beats runtime fetch on this cluster.** The long-lived Kind
  cluster's egress is known-flaky (pods can't resolve `host.docker.internal`;
  external egress is unreliable). Baking the artifacts in removes every
  startup-time fetch that could fail.

## Considered alternatives

- **Separate model-inference service, P-classification calls it over HTTP.**
  Cleaner isolation and independent scaling, and kept genuinely cheap by the
  narrow "detect entities in text" seam. Rejected *for now*, not forever: it
  adds a Deployment, a service contract, a network hop, and an unavailability
  failure mode for no first-increment benefit. Because P-classification talks
  to the model only through that narrow seam, moving to a remote call later is
  a localized change, not a redesign — so this decision is deliberately
  reversible.
- **Mount weights from a PVC / MinIO via an init container, or pull from HF Hub
  at startup.** Leaner image, independently versioned weights. Rejected: splits
  "the model" across image + external storage, decouples config from the model
  version (reintroducing tag-drift risk), and adds a runtime fetch that can
  fail on this cluster's egress.

## Consequences

- A model retrain requires rebuilding and reloading the classification image;
  there is no hot weight swap.
- The classification image carries ~500 MB of weights on top of the torch
  stack — accepted as the cost of a self-contained, drift-free unit.
- The narrow text-in/findings-out seam is a load-bearing reversibility hook:
  keep it narrow so the in-process → service move stays localized.
