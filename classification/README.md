# Classification Pipeline

## Overview

**`run_classification()` is the primary way to run the classification pipeline.** Import it directly in your code to classify text strings or `{index, text}` JSONL files without dealing with CLI flags.

`run_classification.py` is the second way — it accepts a `{index, text}` JSONL file via the command line.

Both options run the trained NER model to detect entity spans (stage 1), then apply the enhanced classification logic to produce sensitivity levels, regulatory tags, and identity bundle detection (stage 2).

For script/CLI documentation, see [README_SCRIPT.md](README_SCRIPT.md). For classification logic, sensitivity levels, identity bundles, and output formats, see [README_LOGIC.md](README_LOGIC.md).

---

## Prerequisites

Install all dependencies via pip (works with pip, conda, or uv):

```bash
# macOS / Linux / powershell
pip install -r requirements.txt
```

> **GPU users:** The default `torch` from PyPI is CPU-only. See the comment in
> `requirements.txt` for instructions on installing a CUDA-enabled build before
> running `pip install -r requirements.txt`.

---

## Input / Output Spec

### Function parameters

All parameters of `run_classification()`:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `input` | `str \| List[str]` | `None` | Raw text to classify — a single string or a list of strings. Takes precedence over `input_file` when both are supplied. |
| `input_file` | `str` | `None` | Path to a `{index, text}` JSONL file. Used only when `input` is `None`. |
| `model_folder` | `str` | `'model/20260304'` | Path to the NER model directory. |
| `intermediate_path` | `str` | `None` | Where to write the intermediate `{index, text, entities}` JSONL. Defaults to `<model_folder>/pred_output/<input_stem>_with_entities.jsonl`. |
| `csv_metadata_path` | `str` | `'inputs/EntityTypesForTokenClassification_with_tags.csv'` | Path to the entity taxonomy CSV. |
| `config_path` | `str` | `'config/classification_config.json'` | Path to the classification config JSON. |
| `validate_tags` | `bool` | `False` | When `True`, validates predicted entity tags against the taxonomy CSV. |
| `save_output` | `bool` | `False` | When `True`, writes CSV and JSON result files to disk under `output_dir`. |
| `output_dir` | `str` | `'output'` | Root output directory used when `save_output=True`. |

### JSONL record format

When using `input_file=`, each line must be a JSON object with two fields:

| Field | Type | Description |
|---|---|---|
| `index` | `int` | Zero-based record identifier used to correlate input ↔ output. |
| `text` | `str` | The raw text to classify. |

```jsonl
{"index": 0, "text": "John Smith lives at 42 Main St, Springfield."}
{"index": 1, "text": "Contact support@example.com or call 555-123-4567."}
```

When using `input=`, the API wraps your string(s) into this format internally.

---

### Output records

`run_classification()` returns a `ClassificationResult` object.

### `ClassificationResult` fields

| Field | Type | Description |
|---|---|---|
| `records` | `List[Dict]` | Rich per-record output with full entity details and summary. See [Output records](#output-records) for the complete schema. |
| `csv_records` | `List[Dict]` | Flat tabular version (mirrors the CSV columns). Complex fields such as `entities` and `regulatory_tags` are JSON-serialised strings. |
| `intermediate_path` | `Path` | NER-annotated intermediate JSONL from Stage 1. |
| `output_dir` | `Optional[Path]` | Root output folder; `None` if `save_output=False`. |
| `csv_path` | `Optional[Path]` | Written CSV file path; `None` if `save_output=False`. |
| `json_path` | `Optional[Path]` | Written JSON file path; `None` if `save_output=False`. |

---

 `records` is its primary field — a list of dicts, one per input record. Each dict has four top-level keys:

| Key | Type | Description |
|---|---|---|
| `index` | `int` | Matches the input record's `index`. |
| `text` | `str` | The original input text, reproduced verbatim. |
| `entities` | `List[Dict]` | One object per detected entity span. See [Entity object](#entity-object) below. |
| `summary` | `Dict` | Document-level classification aggregated across all entities. See [Summary object](#summary-object) below. |

---



#### Entity object

Each item in the `entities` list describes a single detected span:

| Field | Type | Description |
|---|---|---|
| `entity_type` | `str` | Short entity tag from the taxonomy (e.g. `"PN"`, `"SSN"`, `"EMAIL"`). |
| `start` | `int` | Character start offset in `text` (inclusive). |
| `end` | `int` | Character end offset in `text` (exclusive). |
| `text` | `str` | The extracted span text. |
| `domain` | `str` | Broad domain (e.g. `"person"`, `"medical"`, `"finance"`). |
| `category` | `str` | Fine-grained category (e.g. `"person.govID"`, `"medical.identifiers"`). |
| `regulatory_tags` | `List[str]` | Applicable regulations — one or more of `"PII"`, `"PHI"`, `"PCI"`, `"CREDENTIALS"`, `"PI"`. |
| `identifier_type` | `str` | `"PID"` (person ID), `"OPID"` (operational ID), or `"DATA"`. |
| `sensitivity_level` | `str` | `"PUBLIC"`, `"INTERNAL"`, `"CONFIDENTIAL"`, or `"RESTRICTED"`. |
| `confidence` | `float` | Model confidence score (currently always `1.0` after Stage 2 enrichment). |
| `is_personalized` | `bool` | `true` if this entity type is inherently linked to a specific individual. |

---

#### Summary object

The `summary` dict aggregates the classification across all entities in the record:

| Field | Type | Description |
|---|---|---|
| `sensitivity_level` | `str` | Max sensitivity across all entities; upgraded to `"RESTRICTED"` if an identity bundle is detected. |
| `domains` | `List[str]` | Sorted unique domains present (e.g. `["finance", "person"]`). |
| `primary_domain` | `str` | Highest-priority domain (medical > finance > person > others). |
| `regulatory_tags` | `List[str]` | Sorted union of all entity `regulatory_tags`. |
| `entities` | `List[str]` | Sorted unique entity type tags present in this record. |
| `identifier_types_present` | `List[str]` | Sorted unique identifier types (e.g. `["DATA", "PID"]`). |
| `contains_identity_bundle` | `bool` | `true` if two or more entities form a recognised identity pattern (name+DOB, name+address, etc.). |
| `is_personalized` | `bool` | `true` if at least one entity has `is_personalized=true`. |
| `personalized_entity_types` | `List[str]` | Sorted tags of entities that have `is_personalized=true`. |
| `reasons` | `List[str]` | Human-readable explanation of the final sensitivity level. |

---

#### Example output record *(synthetic)*

```json
{
  "index": 0,
  "text": "John Smith, DOB 1990-03-15, lives at 42 Main St, Springfield.",
  "entities": [
    {
      "entity_type": "PN",
      "start": 0,
      "end": 10,
      "text": "John Smith",
      "domain": "person",
      "category": "person.name",
      "regulatory_tags": ["PII"],
      "identifier_type": "PID",
      "sensitivity_level": "CONFIDENTIAL",
      "confidence": 1.0,
      "is_personalized": true
    },
    {
      "entity_type": "DOB",
      "start": 17,
      "end": 27,
      "text": "1990-03-15",
      "domain": "person",
      "category": "person.govID",
      "regulatory_tags": ["PII"],
      "identifier_type": "PID",
      "sensitivity_level": "RESTRICTED",
      "confidence": 1.0,
      "is_personalized": true
    }
  ],
  "summary": {
    "sensitivity_level": "RESTRICTED",
    "domains": ["person"],
    "primary_domain": "person",
    "regulatory_tags": ["PII"],
    "entities": ["DOB", "PN"],
    "identifier_types_present": ["PID"],
    "contains_identity_bundle": true,
    "is_personalized": true,
    "personalized_entity_types": ["DOB", "PN"],
    "reasons": [
      "Identity bundle detected: name_dob",
      "RESTRICTED entities: DOB"
    ]
  }
}
```

---

## Importable API - Examples

`run_classification()` is the **recommended entry point** for programmatic use — no subprocess, no CLI required.

```python
from run_classification import run_classification
```

### From a single text string *(most common)*

```python
result = run_classification(input="John Smith lives at 42 Main St, NY")
print(result.records[0]["summary"])
```

### From a list of text strings

```python
result = run_classification(input=[
    "John Smith lives at 42 Main St",
    "Contact us at support@example.com or call 555-123-4567",
])
for record in result.records:
    print(record["index"], record["summary"]["sensitivity_level"])
```

### From a JSONL file

Use `input_file=` when you already have a pre-built `{index, text}` JSONL on disk:

```python
result = run_classification(input_file="inputs/polar_data_v4.jsonl")
print(result.records[0]["summary"]["sensitivity_level"])

import json
print(json.dumps(result.records, indent=2))
```

### With file output

```python
result = run_classification(
    input_file="inputs/polar_data_v4.jsonl",
    save_output=True,
    output_dir="output/experiment_1",
)
print(result.json_path)   # Path to the written JSON file
print(result.csv_path)    # Path to the written CSV file
```



## NER Model

- **Base model**: `ibm-granite/granite-embedding-125m-english`
- **Fine-tuned for**: token classification over the project's entity taxonomy
- **Model directory**: `model/20260304/`
- **Entity taxonomy**: [`inputs/EntityTypesForTokenClassification_with_tags.csv`](inputs/EntityTypesForTokenClassification_with_tags.csv) — defines all 78 supported entity types, their domains, regulatory tags, and classification metadata

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'torch'`**
→ Run `pip install -r requirements.txt`. If using conda, activate your environment first.

**`Input file not found`**
→ Check that `--input-file` is a valid path relative to the repo root.

**`Config file not found`**
→ Run `python config/generate_config.py` to generate `config/classification_config.json`.

**Pipeline interrupted mid-run**
→ Re-run the same command. Stage 1 will resume from the last checkpoint (already-processed records are skipped automatically).

---

## See Also

- [README_SCRIPT.md](README_SCRIPT.md) — Script/CLI reference: flags, pipeline diagram, examples, running stages independently
- [README_LOGIC.md](README_LOGIC.md) — Classification logic: entity rules, identity bundles, regulatory compliance, output schemas
