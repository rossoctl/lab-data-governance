"""The P-classification config is loaded as baked-in data (issue #78).

The classifier's config artifacts — the identity-bundle patterns + sensitivity
hierarchy (``classification_config.json``) and the NER-tag metadata
(``EntityTypesForTokenClassification_with_tags.csv``) — ship *inside* the package
so the library is self-contained (ADR-0023: config baked into the image as a unit
with the model). These tests pin that the loaders read the packaged copies with
no caller-supplied file path, and that the CSV metadata parses into the shape the
ported logic consumes (regulatory tags as a list, ``is_personalized`` as a bool).
"""

from __future__ import annotations

from data_governance.processors.classification import config


def test_load_config_returns_bundle_patterns_and_hierarchy() -> None:
    """``load_config`` reads the baked-in classification config with no path arg
    and exposes the identity-bundle patterns and the sensitivity hierarchy the
    document-level aggregation ranks against."""
    cfg = config.load_config()

    assert cfg["classification_hierarchy"] == [
        "PUBLIC",
        "INTERNAL",
        "CONFIDENTIAL",
        "RESTRICTED",
    ]
    pattern_names = {p["name"] for p in cfg["identity_bundle_patterns"]}
    assert {"name_ssn", "name_email"}.issubset(pattern_names)


def test_load_entity_metadata_parses_tags_into_the_logic_shape() -> None:
    """``load_entity_metadata`` reads the baked-in NER-tag CSV keyed by TAG, with
    ``regulatory_tags`` parsed from its JSON column into a list and
    ``is_personalized`` coerced to a bool — the shape the ported classification
    logic reads per **Finding**."""
    meta = config.load_entity_metadata()

    ssn = meta["SSN"]
    assert ssn["domain"] == "person"
    assert ssn["data_type"] == "personID"
    assert ssn["regulatory_tags"] == ["PII"]
    assert ssn["is_personalized"] is True

    # A CREDENTIALS-tagged entity keeps both of its regulatory tags.
    assert meta["PW"]["regulatory_tags"] == ["PI", "CREDENTIALS"]
