"""The ported classification logic matches the reference batch tool (issue #78).

The classification logic — per-**Finding** sensitivity, identity-bundle detection,
and the document-level roll-up (``sensitivity_level``, ``regulatory_tags``,
``primary_domain``, ``is_personalized``) — is ported out of the reference batch
tool ``classification/process_entities_enhanced.py`` into :mod:`.logic` as clean
library functions (text + annotations + loaded config in, **Findings** + summary
out; no CLI/print/exit/file-path I/O). The reference tool stays as the offline-eval
harness (mirroring how P-interactions was ported from its verified prototype).

The acceptance headline (issue #78): *given the same text + annotations, the
ported logic produces the same Findings + summary the reference tool does.* These
tests drive the reference tool's own functions (loaded from its file by path,
since ``classification/`` is not an importable package) and the ported
:func:`logic.classify_text` on shared fixtures and assert equality on the
domain-meaningful fields — so parity is checked against the *actual* reference
source, not a hand-copy.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from data_governance.processors.classification import config, logic

_REPO_ROOT = Path(__file__).resolve().parents[3]
_REFERENCE_TOOL = _REPO_ROOT / "classification" / "process_entities_enhanced.py"


def _load_reference_module():
    """Import the reference batch tool from its file (it is a standalone script,
    not an installable package) so parity is measured against the real source."""
    spec = importlib.util.spec_from_file_location(
        "reference_process_entities_enhanced", _REFERENCE_TOOL
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Shared fixtures: (text, annotations) pairs spanning the sensitivity ladder,
# identity bundles, personalization, cross-domain priority, and the empty case.
_FIXTURES: list[tuple[str, list[list]]] = [
    # No annotations at all → the clean PUBLIC / zero-finding document.
    ("Just some ordinary prose with nothing sensitive in it.", []),
    # Name + SSN → RESTRICTED, identity bundle, personalized.
    ("John Smith SSN is 123-45-6789 today", [[0, 10, "PN"], [18, 29, "SSN"]]),
    # A lone email (PII) → CONFIDENTIAL, no bundle.
    ("reach me at john@example.com anytime", [[12, 28, "EMAIL"]]),
    # Name + email → identity bundle forces RESTRICTED.
    ("Jane Doe jane@doe.org", [[0, 8, "PN"], [9, 21, "EMAIL"]]),
    # Medical + finance mix → primary_domain prioritizes by max count.
    (
        "MRN-12345678 dx and IBAN GB29 and a Diabetes note",
        [[0, 12, "MRN"], [20, 24, "IBAN"], [36, 44, "DIS"]],
    ),
    # An operational identifier alone → INTERNAL.
    ("routing 021000021 on file", [[8, 17, "RN"]]),
    # A credentials tag → RESTRICTED via CREDENTIALS regulatory tag.
    ("the password is hunter2guessme!", [[16, 31, "PW"]]),
    # An unknown tag not present in the CSV → reference defaults it to INTERNAL.
    ("mystery token blah", [[0, 13, "NOT_A_REAL_TAG"]]),
]


def _reference_verdict(text: str, annotations: list[list]):
    ref = _load_reference_module()
    cfg = config.load_config()
    meta = config.load_entity_metadata()
    enhanced = [ref.enhance_entity(a, text, meta, cfg) for a in annotations]
    bundles = ref.detect_identity_bundles(enhanced, cfg)
    summary = ref.create_summary(enhanced, bundles, cfg)
    return enhanced, summary


@pytest.mark.parametrize("text,annotations", _FIXTURES)
def test_document_summary_matches_reference(text: str, annotations: list[list]) -> None:
    """The document-level verdict the ported logic derives equals the reference
    tool's summary on every shared fixture: same sensitivity, regulatory tags,
    primary domain, identity-bundle flag, and personalization."""
    cfg = config.load_config()
    meta = config.load_entity_metadata()

    _, ref_summary = _reference_verdict(text, annotations)
    result = logic.classify_text(text, annotations, meta, cfg)

    assert result.sensitivity_level == ref_summary["sensitivity_level"]
    assert result.regulatory_tags == ref_summary["regulatory_tags"]
    # The reference stores "" for no primary domain; the package uses None.
    assert (result.primary_domain or "") == ref_summary["primary_domain"]
    assert result.contains_identity_bundle == ref_summary["contains_identity_bundle"]
    assert result.is_personalized == ref_summary["is_personalized"]


@pytest.mark.parametrize("text,annotations", _FIXTURES)
def test_findings_match_reference(text: str, annotations: list[list]) -> None:
    """Each **Finding** the ported logic produces equals the reference tool's
    enhanced entity on the domain-meaningful fields — the detected tag, its char
    region, the extracted text, and the derived sensitivity attributes. (A
    finding's type is an NER *tag*; the reference calls the same field
    ``entity_type``.)"""
    meta = config.load_entity_metadata()
    cfg = config.load_config()

    ref_enhanced, _ = _reference_verdict(text, annotations)
    findings = logic.classify_text(text, annotations, meta, cfg).findings

    assert len(findings) == len(ref_enhanced)
    for finding, ref in zip(findings, ref_enhanced):
        assert finding["tag"] == ref["entity_type"]
        assert finding["start"] == ref["start"]
        assert finding["end"] == ref["end"]
        assert finding["text"] == ref["text"]
        assert finding["sensitivity_level"] == ref["sensitivity_level"]
        assert finding["regulatory_tags"] == ref["regulatory_tags"]
        assert finding["identifier_type"] == ref["identifier_type"]
