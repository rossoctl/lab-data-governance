"""Loading the baked-in P-classification config as data (issue #78).

The reference batch tool (``classification/process_entities_enhanced.py``) loaded
its config by CLI file-path argument and called ``sys.exit`` on any read failure.
Ported into the package these become plain library loaders over the artifacts
that ship *inside* the package (ADR-0023: the ``classification_config.json``
identity-bundle patterns + sensitivity hierarchy, and the
``EntityTypesForTokenClassification_with_tags.csv`` NER-tag metadata, are baked in
as one unit with the model). No CLI, no ``print``, no ``sys.exit``, no
caller-supplied path — the artifacts live next to this module and are resolved
relative to ``__file__``, the same way the API resolves its bundled UI assets.

``load_entity_metadata`` returns the metadata keyed by NER **tag** — the detected
type of a **Finding** (CONTEXT.md: a finding's type is an NER tag, never an
**Entity**). The parsed shape (``regulatory_tags`` as a list, ``is_personalized``
as a bool) is exactly what :mod:`.logic` reads per finding.
"""

from __future__ import annotations

import csv
import functools
import json
from pathlib import Path
from typing import Any

# The baked-in config artifacts, resolved relative to this module (mirrors
# ``data_governance.api``'s bundled-UI resolution). ADR-0023 bakes both into the
# classification image; here they live in the package so the library is
# self-contained and importable without the image.
_CONFIG_DIR = Path(__file__).parent / "_config_data"
_CLASSIFICATION_CONFIG = _CONFIG_DIR / "classification_config.json"
_ENTITY_METADATA_CSV = _CONFIG_DIR / "EntityTypesForTokenClassification_with_tags.csv"


# Both loaders are memoized for the process lifetime: the artifacts are
# immutable baked-in data (a config/model upgrade is a full image rebuild +
# process restart — ADR-0023/0024), so the per-payload classification hot path
# reads them from disk exactly once, not once per payload. Callers must treat the
# returned objects as read-only (the ported logic in :mod:`.logic` copies the
# lists it derives and never mutates config/metadata).
@functools.lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    """Load the classification config: the identity-bundle patterns and the
    ``PUBLIC < INTERNAL < CONFIDENTIAL < RESTRICTED`` sensitivity hierarchy the
    document-level aggregation ranks against."""
    with _CLASSIFICATION_CONFIG.open(encoding="utf-8") as f:
        return json.load(f)


@functools.lru_cache(maxsize=1)
def load_entity_metadata() -> dict[str, dict[str, Any]]:
    """Load the NER-tag metadata, keyed by TAG.

    Each entry carries the ``domain``, ``data_type``, ``category``,
    ``regulatory_tags`` (parsed from the CSV's JSON column into a list; malformed
    JSON falls back to an empty list, matching the reference tool), and
    ``is_personalized`` (the ``Is Personalized`` column coerced to a bool). This
    is the shape :mod:`.logic` reads to classify each **Finding**.
    """
    metadata: dict[str, dict[str, Any]] = {}
    with _ENTITY_METADATA_CSV.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tag = row.get("TAG", "").strip()
            if not tag:
                continue
            regulatory_tags_str = row.get("regulatory_tags", "[]").strip()
            try:
                regulatory_tags = json.loads(regulatory_tags_str)
            except json.JSONDecodeError:
                regulatory_tags = []
            metadata[tag] = {
                "domain": row.get("Domain", "").strip(),
                "data_type": row.get("Data Type", "").strip(),
                "category": row.get("Category", "").strip(),
                "regulatory_tags": regulatory_tags,
                "is_personalized": row.get("Is Personalized", "").strip() == "1",
            }
    return metadata
