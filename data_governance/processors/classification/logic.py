"""The ported classification logic (issue #78).

Clean library functions ported out of the reference batch tool
``classification/process_entities_enhanced.py`` (the offline-eval harness, kept
intact). The CLI scaffolding — ``argparse``, ``print``, ``sys.exit``, JSONL/CSV
file I/O, the timestamped output writers — is gone; what remains is the pure
mapping the tool always was underneath: **Classifiable text** + NER annotations
(``(start, end, tag)`` triples) + loaded config in, **Findings** + a document-level
summary out.

Terminology follows CONTEXT.md. A detected item is a **Finding**, never a "span"
(a **Span** is the OTEL row). A finding's ``entity_type`` is its detected NER type
(``PN``, ``SSN``); despite the field name it is *not* an **Entity** (the
interaction participant) — the name matches the reference tool, the API's inlined
``findings`` JSON, and the UI's ``Finding`` wire type, so the one key spans the
whole stack (CONTEXT.md **Finding** pins it as the stored/served contract). The
reference tool named the whole detected item an "entity"; the port keeps the
``entity_type`` field name but renames the item to **Finding**, holding the
sensitivity derivation byte-for-byte identical, so
``tests/processors/classification/test_logic_parity.py`` proves equality against
the real reference source.
"""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass(frozen=True)
class Classification:
    """The document-level verdict over one **Classifiable text**, plus its
    **Findings** — the pure-logic output the driver's :class:`~.verdict.Verdict`
    is built from.

    ``primary_domain`` is ``None`` when no findings carry a domain (the reference
    tool used ``""``; the package prefers ``None`` to match the nullable DB column
    and :class:`~.verdict.Verdict`).
    """

    sensitivity_level: str
    regulatory_tags: list[str]
    contains_identity_bundle: bool
    is_personalized: bool
    primary_domain: str | None
    findings: list[dict[str, Any]]


def _base_classification(
    tag: str, entity_metadata: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """The ``regulatory_tags`` + ``identifier_type`` for one NER tag, inferring
    ``identifier_type`` from the CSV ``data_type`` column. An unknown tag (absent
    from the metadata) defaults to no tags / ``NON_ID`` — the reference tool's
    conservative fallback for a tag the model emits but the CSV lacks (ADR-0023
    calls this drift a known hazard). The finding's sensitivity is derived from
    these two by :func:`build_finding`, never stored here."""
    if tag not in entity_metadata:
        return {"identifier_type": "NON_ID", "regulatory_tags": []}

    metadata = entity_metadata[tag]
    data_type = metadata["data_type"]

    if data_type == "personID":
        identifier_type = "PID"
    elif data_type == "operationalID":
        identifier_type = "OPID"
    else:
        identifier_type = "DATA"

    return {
        "identifier_type": identifier_type,
        "regulatory_tags": list(metadata["regulatory_tags"]),
    }


def calculate_finding_sensitivity_level(
    regulatory_tags: list[str], identifier_type: str
) -> str:
    """Sensitivity level for one **Finding** from its regulatory tags and
    identifier type, on the ``PUBLIC < INTERNAL < CONFIDENTIAL < RESTRICTED``
    ladder. Credentials/PHI/PCI or a person-ID is RESTRICTED; PII is CONFIDENTIAL;
    PI or an operational-ID is INTERNAL; everything else PUBLIC."""
    if (
        "CREDENTIALS" in regulatory_tags
        or "PHI" in regulatory_tags
        or "PCI" in regulatory_tags
    ):
        return "RESTRICTED"
    if identifier_type == "PID":
        return "RESTRICTED"
    if "PII" in regulatory_tags:
        return "CONFIDENTIAL"
    if "PI" in regulatory_tags:
        return "INTERNAL"
    if identifier_type == "OPID":
        return "INTERNAL"
    return "PUBLIC"


def build_finding(
    annotation: tuple[int, int, str] | list,
    text: str,
    entity_metadata: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build one **Finding** from a detector ``(start, end, tag)`` annotation and
    the **Classifiable text**: the detected tag, its char region, the extracted
    substring, its CSV-derived metadata, and its derived sensitivity attributes.

    ``start``/``end`` are offsets into *text*; an out-of-range region yields an
    empty ``text`` (matching the reference tool's defensive slice)."""
    start, end, tag = annotation[0], annotation[1], annotation[2]
    base = _base_classification(tag, entity_metadata)
    metadata = entity_metadata.get(tag, {})

    # Recompute the finding's sensitivity from its regulatory tags + identifier
    # type — NOT the base's ``sensitivity_level`` field. This matches the
    # reference tool's ``enhance_entity``, which recomputes here rather than
    # trusting ``get_base_classification``'s level; the two diverge for an
    # unknown tag (base is INTERNAL / NON_ID, but the recompute over [] / NON_ID
    # is PUBLIC), and the recomputed value is the one that lands on the finding.
    sensitivity = calculate_finding_sensitivity_level(
        base["regulatory_tags"], base["identifier_type"]
    )

    return {
        # The finding's detected NER-tag type. Keyed ``entity_type`` (the name
        # the reference tool, the API's inlined ``findings`` JSON, and the UI's
        # ``Finding`` wire type all use) — NOT an **Entity** (the interaction
        # participant); the key name is the stored/served contract, pinned in
        # CONTEXT.md's **Finding** definition.
        "entity_type": tag,
        "start": start,
        "end": end,
        "text": text[start:end] if start < len(text) and end <= len(text) else "",
        "domain": metadata.get("domain", "unknown"),
        "category": metadata.get("category", ""),
        "regulatory_tags": list(base["regulatory_tags"]),
        "identifier_type": base["identifier_type"],
        "sensitivity_level": sensitivity,
        "is_personalized": metadata.get("is_personalized", False),
    }


def detect_identity_bundles(
    findings: list[dict[str, Any]], config: dict[str, Any]
) -> list[dict[str, Any]]:
    """The identity-bundle patterns whose required tag set is a subset of the
    tags present across *findings* (e.g. ``PN`` + ``SSN`` → the ``name_ssn``
    bundle). A detected bundle forces the document to RESTRICTED."""
    present_tags = {f["entity_type"] for f in findings}
    detected = []
    for pattern in config["identity_bundle_patterns"]:
        if set(pattern["entities"]).issubset(present_tags):
            detected.append(
                {
                    "name": pattern["name"],
                    "description": pattern["description"],
                    "entities": pattern["entities"],
                }
            )
    return detected


def aggregate_domains(findings: list[dict[str, Any]]) -> tuple[list[str], str | None]:
    """The sorted set of domains across *findings* and the primary domain:
    ``medical`` or ``finance`` (whichever has more findings) wins over
    ``person``; else ``person``; else the most common domain. ``None`` when there
    are no findings."""
    if not findings:
        return [], None

    domain_counts: dict[str, int] = {}
    for finding in findings:
        domain = finding["domain"]
        domain_counts[domain] = domain_counts.get(domain, 0) + 1

    domains = sorted(domain_counts.keys())

    priority = []
    if "medical" in domain_counts:
        priority.append(("medical", domain_counts["medical"]))
    if "finance" in domain_counts:
        priority.append(("finance", domain_counts["finance"]))

    if priority:
        primary_domain = max(priority, key=lambda x: x[1])[0]
    elif "person" in domain_counts:
        primary_domain = "person"
    else:
        primary_domain = max(domain_counts.items(), key=lambda x: x[1])[0]

    return domains, primary_domain


def aggregate_regulatory_tags(findings: list[dict[str, Any]]) -> list[str]:
    """The sorted set of every regulatory tag carried by any **Finding**."""
    all_tags: set[str] = set()
    for finding in findings:
        all_tags.update(finding["regulatory_tags"])
    return sorted(all_tags)


def aggregate_document_sensitivity(
    findings: list[dict[str, Any]],
    identity_bundles: list[dict[str, Any]],
    config: dict[str, Any],
) -> str:
    """The document sensitivity: the max **Finding** sensitivity on the config's
    hierarchy, promoted to RESTRICTED when any identity bundle is present.
    ``PUBLIC`` when there are no findings."""
    if not findings:
        return "PUBLIC"

    hierarchy = config["classification_hierarchy"]
    max_level = max(
        (f["sensitivity_level"] for f in findings), key=lambda x: hierarchy.index(x)
    )
    if identity_bundles:
        max_level = "RESTRICTED"
    return max_level


def classify_text(
    text: str,
    annotations: list[tuple[int, int, str]] | list[list],
    entity_metadata: dict[str, dict[str, Any]],
    config: dict[str, Any],
) -> Classification:
    """Classify one **Classifiable text** given its NER annotations and loaded
    config — the ported logic's top-level entry point.

    Builds a **Finding** per annotation, detects identity bundles across the
    finding set, and rolls the findings up to the document-level verdict. With no
    annotations this is a real ``PUBLIC`` / zero-**Findings** classification —
    never a null (ADR-0024)."""
    findings = [build_finding(a, text, entity_metadata) for a in annotations]
    identity_bundles = detect_identity_bundles(findings, config)

    _, primary_domain = aggregate_domains(findings)
    return Classification(
        sensitivity_level=aggregate_document_sensitivity(
            findings, identity_bundles, config
        ),
        regulatory_tags=aggregate_regulatory_tags(findings),
        contains_identity_bundle=len(identity_bundles) > 0,
        is_personalized=any(f["is_personalized"] for f in findings),
        primary_domain=primary_domain,
        findings=findings,
    )
