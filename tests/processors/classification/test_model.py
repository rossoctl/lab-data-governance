"""The in-process NER model's pure token-grouping port (issue #79).

The fine-tuned NER model emits one BIO label per sub-word token; turning that
per-token label stream into a list of **Finding** regions — ``(start, end, tag)``
:data:`~data_governance.processors.classification.detector.Annotation` triples,
char offsets into the **Classifiable text** — is pure Python (no torch): buffer
sub-words back into words, coalesce ``B-``/``I-`` runs of the same tag, merge runs
that straddle a small gap, and drop the special-token slots.

This logic is ported verbatim (behaviour-preserving) from the reference eval
harness (``classification/model/utils/predict_entities.py``), which stays intact
as the offline harness. The port lives in :mod:`.model` so it is importable and
testable *without* torch or the ~500 MB weights (those are baked into the
classification image, ADR-0022/0023). These tests pin the observable contract:
label stream + offsets in, **Finding** annotations out — a finding is a
``(start, end, tag)`` region, the tag is an NER tag, never an **Entity**
(CONTEXT.md).

The expected annotations below match the reference harness's output on the same
inputs, and are the very offsets the verdict/detector seam tests already inject
(e.g. ``(0, 10, "PN")`` / ``(11, 22, "SSN")``), so the port stays consistent with
the whole classification stack.
"""

from __future__ import annotations

from data_governance.processors.classification import model


def _group(text, tokens, offsets, labels, word_ids):
    """Run the pure grouping over one tokenized chunk (confidences are uniform —
    they do not affect the region boundaries)."""
    return model.group_annotations(
        text=text,
        tokens=tokens,
        offset_mapping=offsets,
        labels=labels,
        confidences=[0.9] * len(tokens),
        word_ids=word_ids,
    )


def test_groups_a_name_and_an_ssn_into_two_findings() -> None:
    """A ``B-PN I-PN`` run and a ``B-SSN I-SSN...`` run coalesce into two
    **Findings**, each a ``(start, end, tag)`` region over the classifiable text —
    the reference harness's output for this input, and the offsets the seam tests
    inject."""
    text = "John Smith 123-45-6789"
    annotations = _group(
        text,
        ["<s>", "John", "Smith", "123", "-", "45", "-", "6789", "</s>"],
        [(0, 0), (0, 4), (5, 10), (11, 14), (14, 15), (15, 17), (17, 18), (18, 22), (0, 0)],
        ["O", "B-PN", "I-PN", "B-SSN", "I-SSN", "I-SSN", "I-SSN", "I-SSN", "O"],
        [None, 0, 1, 2, 2, 3, 3, 4, None],
    )
    assert annotations == [(0, 10, "PN"), (11, 22, "SSN")]


def test_all_outside_labels_yield_no_findings() -> None:
    """A chunk with only ``O`` labels detects nothing — a real zero-**Findings**
    result (the clean-payload path; ADR-0024)."""
    text = "book a flight"
    annotations = _group(
        text,
        ["<s>", "book", "a", "flight", "</s>"],
        [(0, 0), (0, 4), (5, 6), (7, 13), (0, 0)],
        ["O", "O", "O", "O", "O"],
        [None, 0, 1, 2, None],
    )
    assert annotations == []


def test_single_token_finding_spans_its_offsets() -> None:
    """A lone ``B-EMAIL`` token becomes one **Finding** whose region is that
    token's char offsets — matching the ``(6, 18, "EMAIL")`` the seam tests
    inject."""
    text = "email jane@doe.org"
    annotations = _group(
        text,
        ["<s>", "email", "jane@doe.org", "</s>"],
        [(0, 0), (0, 5), (6, 18), (0, 0)],
        ["O", "O", "B-EMAIL", "O"],
        [None, 0, 1, None],
    )
    assert annotations == [(6, 18, "EMAIL")]
