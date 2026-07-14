"""The real verdict path (issue #78): project → detect → aggregate.

Issue #77 shipped :func:`verdict.classify` as a stub that ignored the payload and
returned ``PUBLIC``. Issue #78 replaces it with the real path — project the
payload's ``content`` into its **Classifiable text** (the **Text projection
rule**), run a **Detector** over that text to get **Finding** annotations, and
aggregate those into the document-level verdict (the ported logic) — behind the
*same* signature and the same driver write path.

The NER model is not wired until #79, so ``classify`` detects through the injected
:class:`~data_governance.processors.classification.detector.Detector` seam,
defaulting to :class:`NullDetector` (finds nothing). These tests drive both the
default (no model → real clean verdict) and a fake detector (findings → real
non-PUBLIC verdict), proving the whole path independently of the model. Findings
detect in **Classifiable text** offsets, and a finding's type is an NER *tag*,
never an **Entity** (CONTEXT.md).
"""

from __future__ import annotations

from data_governance.processors.classification import verdict


def test_default_detector_yields_a_real_public_zero_finding_verdict() -> None:
    """With the default no-op detector a payload gets a real ``PUBLIC`` /
    zero-**Findings** verdict — never a null (ADR-0024) — even though the content
    projects to real prose. model_version stays 1 until #79 bumps it."""
    content = {"messages": [{"message.role": "user", "message.content": "Book me a flight."}]}
    v = verdict.classify("h0", "llm_chat_prompt", content)

    assert v.sensitivity_level == "PUBLIC"
    assert v.findings == []
    assert v.regulatory_tags == []
    assert v.contains_identity_bundle is False
    assert v.is_personalized is False
    assert v.primary_domain is None
    assert v.model_version == 1


def test_empty_payload_is_a_real_public_verdict() -> None:
    """A payload with no projectable text is still classified — a real clean
    verdict, not a null and not a skip (ADR-0024: null means only 'not yet
    processed')."""
    v = verdict.classify("empty", "unknown", {})
    assert v.sensitivity_level == "PUBLIC"
    assert v.findings == []


def test_injected_detector_drives_a_real_non_public_verdict() -> None:
    """Injecting a detector that finds a name + SSN in the projected text yields a
    real RESTRICTED verdict with an identity bundle and personalization — the whole
    path (project → detect → aggregate) exercised without a model. #79 swaps the
    real NER model in for this fake, unchanged elsewhere."""
    content = {"messages": [{"message.role": "user", "message.content": "John Smith 123-45-6789"}]}

    class FakeDetector:
        def detect(self, text: str) -> list[tuple[int, int, str]]:
            # Offsets into the projected Classifiable text ("John Smith 123-45-6789").
            return [(0, 10, "PN"), (11, 22, "SSN")]

    v = verdict.classify("h1", "llm_chat_prompt", content, detector=FakeDetector())

    assert v.sensitivity_level == "RESTRICTED"
    assert v.contains_identity_bundle is True
    assert v.is_personalized is True
    assert set(v.regulatory_tags) == {"PII"}
    assert v.primary_domain == "person"
    assert len(v.findings) == 2
    assert {f["tag"] for f in v.findings} == {"PN", "SSN"}
    assert v.model_version == 1


def test_findings_offsets_index_the_projected_text_not_the_jsonb() -> None:
    """A **Finding**'s ``text`` is sliced from the projected **Classifiable
    text**, so the detector's offsets index the projection — not the raw JSONB.
    (The projected text of this payload is exactly its message body.)"""
    content = {"messages": [{"message.role": "user", "message.content": "email jane@doe.org"}]}

    class FakeDetector:
        def detect(self, text: str) -> list[tuple[int, int, str]]:
            assert text == "email jane@doe.org"  # projection ran before detection
            return [(6, 18, "EMAIL")]

    v = verdict.classify("h2", "llm_chat_prompt", content, detector=FakeDetector())
    assert v.sensitivity_level == "CONFIDENTIAL"
    assert v.findings[0]["text"] == "jane@doe.org"
