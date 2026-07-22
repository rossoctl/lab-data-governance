# Classification Logic — Entity Classification System

For the **importable API** (`run_classification()`), see [README.md](README.md). For **script/CLI usage**, see [README_SCRIPT.md](README_SCRIPT.md).

---

## Overview

This document describes the classification logic applied in Stage 2 of the pipeline. It processes entity-annotated records and produces sensitivity levels, regulatory tags, identity bundle detection, and detailed classification reports.

---

## Features

### Key Capabilities

1. **Entity-Level Classification**
   - Classification level (RESTRICTED/CONFIDENTIAL/INTERNAL/PUBLIC)
   - Identifier type (PID/OPID/DATA)
   - Regulatory tags (PII/PHI/PCI/CREDENTIALS/PI)
   - Domain and category mapping
   - Confidence scores
   - Personalization flag (`is_personalized`) sourced from CSV

2. **Identity Bundle Detection**
   - Detects 9 common identity bundle patterns
   - Examples: name+DOB, name+address, name+email, etc.
   - Automatically upgrades classification to RESTRICTED

3. **Regulatory Compliance**
   - **PHI**: Protected Health Information (defined in CSV)
   - **PCI**: Payment Card Industry data (defined in CSV)
   - **PII**: Personally Identifiable Information (defined in CSV)
   - **CREDENTIALS**: Authentication credentials (defined in CSV)
   - **PI**: Personal Information (defined in CSV)

4. **Document-Level Aggregation**
   - Takes maximum classification from all entities
   - Applies upgrade rules for identity bundles
   - Provides detailed reasoning

5. **Dual Output Formats**
   - **CSV**: Enhanced with new classification columns
   - **JSON**: Detailed entity-level and document-level data

---

## CSV Input Requirements

The entity taxonomy CSV (`inputs/EntityTypesForTokenClassification_with_tags.csv`) must contain these columns:

**Required Columns:**
- `TAG` - Entity type identifier (e.g., PN, SSN, EMAIL)
- `Domain` - Entity domain (person, finance, medical, etc.)
- `Data Type` - Classification basis: `personID`, `operationalID`, or `data`
- `Category` - Entity category (e.g., Name, Government ID, Contact)
- `regulatory_tags` - JSON array of regulatory tags: `["PII"]`, `["PHI"]`, `["PCI"]`, etc.
- `Is Personalized` - `1` if the entity type is considered personalized, empty otherwise

**Example:**
```csv
TAG,Domain,Data Type,Category,regulatory_tags,Is Personalized
PN,person,personID,Name,"[""PII""]",1
SSN,person,personID,Government ID,"[""PII""]",1
EMAIL,person,data,Contact,"[""PII""]",
```

---

## Output Formats

Both outputs are written to a timestamped folder: `output/<input_stem>_<YYYY-MM-DD_HH-MM-SS>/`.

### Enhanced CSV Output

**Columns:**
- `index` - Record index from input JSONL
- `text` - Original text
- `entities` - JSON array: `[[start, end, TAG, text], ...]` (includes extracted text)
- `list_of_entities` - JSON array of TAGs (backward compatible)
- `list_of_domains` - JSON array of unique domains (backward compatible)
- `sensitivity_level` - PUBLIC/INTERNAL/CONFIDENTIAL/RESTRICTED
- `primary_domain` - Dominant domain
- `regulatory_tags` - JSON array of regulatory tags
- `contains_identity_bundle` - Boolean flag
- `identity_bundles` - JSON array of detected bundle names
- `is_personalized` - `True` if at least one entity in the record is personalized

### Detailed JSON Output

**Structure:**
```json
{
  "index": 0,
  "text": "...",
  "entities": [
    {
      "entity_type": "PN",
      "start": 567,
      "end": 579,
      "text": "Sofia Martin",
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
    "domains": ["person", "medical"],
    "primary_domain": "person",
    "regulatory_tags": ["PII", "PHI"],
    "entity_types_present": ["PN", "DOB", "MRN"],
    "identifier_types_present": ["PID"],
    "data_subjects": [],
    "granularity": "RECORD",
    "contains_identity_bundle": true,
    "is_personalized": true,
    "personalized_entity_types": ["PN"],
    "reasons": [
      "Identity bundle detected: name_dob",
      "RESTRICTED entities: PN"
    ]
  }
}
```

---

## Configuration

### CSV-Driven Classification

The system uses a **CSV-driven approach**:
1. All entity types use CSV metadata for classification
2. Config file defines identity bundle patterns and classification hierarchy
3. Regulatory tags (PHI/PCI/PII/CREDENTIALS/PI) are defined in CSV `regulatory_tags` column

### Classification Logic

**Identifier Type Inference (from CSV `Data Type`):**
- `personID` → `PID` (Person Identifier)
- `operationalID` → `OPID` (Operational Identifier)
- `data` → `DATA` (Non-identifying data)

**Classification Level Rules (applied in order):**
1. **RESTRICTED** if:
   - Has `CREDENTIALS`, `PHI`, or `PCI` regulatory tags, OR
   - Identifier type is `PID`
2. **CONFIDENTIAL** if:
   - Has `PII` regulatory tag
3. **INTERNAL** if:
   - Has `PI` regulatory tag, OR
   - Identifier type is `OPID`
4. **PUBLIC** otherwise (for `DATA` type without regulatory tags)

**Note:** Regulatory tags are read from CSV `regulatory_tags` column, not computed dynamically.

### Identity Bundle Patterns

1. `name_dob` - Person name + Date of birth
2. `name_address` - Person name + Address
3. `name_phone` - Person name + Phone
4. `name_email` - Person name + Email
5. `name_ssn` - Person name + SSN
6. `email_phone` - Email + Phone
7. `full_financial` - Name + Bank account + Routing
8. `medical_record` - Name + Medical record number
9. `payment_card_full` - Name + Payment card number

---

## Key Design Decisions

### 1. CSV-Driven Classification
- All entity metadata defined in CSV file
- Config file only for identity bundles and hierarchy
- Easy to maintain and extend

### 2. Hierarchical Classification
- Document level = MAX(entity levels)
- Upgrade rules for identity bundles
- Priority: PUBLIC < INTERNAL < CONFIDENTIAL < RESTRICTED

### 3. Regulatory Tagging
- Regulatory tags defined in CSV `regulatory_tags` column
- Supports: PII, PHI, PCI, CREDENTIALS, PI
- No dynamic computation - explicit definition

### 4. Backward Compatibility
- Preserves original CSV columns (list_of_entities, list_of_domains)
- Adds new columns without breaking existing workflows
- Enhanced entity format includes extracted text

### 5. Dual Output
- CSV for spreadsheet analysis
- JSON for programmatic access with full entity details
- Both contain same classification data

---

## Security & Compliance

### HIPAA Compliance
- PHI entities automatically tagged
- Medical domain + PII → PHI
- RESTRICTED classification enforced

### PCI-DSS Compliance
- Payment card data automatically tagged
- PCI entities → RESTRICTED
- Includes PCN, CVV, expiration dates

### GDPR/CCPA Compliance
- PII entities clearly marked
- Person identifiers tracked
- Classification levels support data minimization

---

## Customization

### Add New Entity Type

1. Add row to CSV: `inputs/EntityTypesForTokenClassification_with_tags.csv`
2. Define columns:
   - `TAG`: Entity identifier
   - `Domain`: Entity domain
   - `Data Type`: `personID`, `operationalID`, or `data`
   - `Category`: Entity category
   - `regulatory_tags`: JSON array like `["PII"]`
   - `Is Personalized`: `1` if the entity type is personalized, leave empty otherwise
3. Re-run processing

### Add New Identity Bundle

Edit `config/classification_config.json`:

```json
{
  "name": "custom_bundle",
  "entities": ["TAG1", "TAG2"],
  "description": "Description"
}
```

### Change Classification Level

Edit the CSV file `inputs/EntityTypesForTokenClassification_with_tags.csv`:

1. Modify `Data Type` column (personID/operationalID/data)
2. Modify `regulatory_tags` column (add/remove tags like PII, PHI, PCI)
3. Re-run processing

---

## Troubleshooting

**Config file not found**
→ Run `python config/generate_config.py`

**Unknown entity TAG**
→ Add the entity to `inputs/EntityTypesForTokenClassification_with_tags.csv` with all required columns

**Wrong classification**
→ Check CSV `Data Type` and `regulatory_tags` columns

**Missing identity bundle**
→ Verify pattern is in `config/classification_config.json` and entities are present in the record

---

## See Also

- [README.md](README.md) — Importable API reference (`run_classification()`)
- [README_SCRIPT.md](README_SCRIPT.md) — Script/CLI reference: flags, pipeline diagram, examples, running stages independently

---

**Last Updated:** 2026-07-08
