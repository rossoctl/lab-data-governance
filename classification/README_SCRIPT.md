# `run_classification.py` — Script Reference

This document describes `run_classification.py` as a script: CLI flags, pipeline mechanics, examples, and how to run the two stages independently. For the **importable API** (`run_classification()`), see [README.md](README.md). For **classification logic** (entity rules, identity bundles, compliance), see [README_LOGIC.md](README_LOGIC.md).

---

## How It Works

```
"John Smith at 42 Main St"  ← text string / list of strings (preferred)
inputs/polar_data_v4.jsonl  ← or a {index, text} JSONL file
  {index, text}                           model/20260304/
         │                                    NER model
         └──────────────┬─────────────────────┘
                        ▼
          predict_entities_from_jsonl()
                        │
                        ▼
   model/20260304/pred_output/polar_data_v4_with_entities.jsonl
   {index, text, entities: [[start, end, TAG], ...]}
                        │
         ┌──────────────┴──────────────────────────────────┐
         │  inputs/EntityTypes..._with_tags.csv             │
         │  config/classification_config.json               │
         └──────────────┬──────────────────────────────────┘
                        ▼
                 process_jsonl()
                        │
                        ▼
     ClassificationResult
       .records          ← rich List[Dict], directly JSON-serialisable
       .csv_records      ← flat tabular List[Dict]
       .intermediate_path
       .output_dir / .csv_path / .json_path  ← set only when save_output=True
```

Both stages run in a **single Python process** via library calls (no subprocesses).

The intermediate `{index, text, entities}` JSONL is written to disk by default at `<model_folder>/pred_output/<stem>_with_entities.jsonl`. This preserves Stage 1's built-in **resume checkpoint** — if the run is interrupted, re-running the pipeline will skip already-processed records.

When raw text strings are passed via `input=`, a temporary JSONL is created automatically and deleted when the call returns. Pass a file path via `input_file=` to process a pre-existing JSONL directly.

---

## CLI Reference

```
python run_classification.py [OPTIONS]
```

| Argument | Default | Description |
|---|---|---|
| `--input-file`, `-i` | *(required)* | Path to the raw `{index, text}` JSONL file |
| `--model-folder` | `model/20260304` | Path to the NER model directory |
| `--intermediate-path` | auto | Where to write the intermediate `{index, text, entities}` JSONL. Defaults to `<model_folder>/pred_output/<stem>_with_entities.jsonl` |
| `--output-dir`, `-o` | `output` | Output directory for final CSV and JSON |
| `--csv-path` | `inputs/EntityTypesForTokenClassification_with_tags.csv` | Entity metadata CSV |
| `--config`, `-c` | `config/classification_config.json` | Classification config JSON |
| `--validate-tags` | `False` | Validate predicted entity tags against the model's known tag list |

> **Note:** The CLI is file-only (`--input-file` is required). For raw text input, use the importable API (`input=`). The CLI always writes output files; to get results in-memory only, use the API with `save_output=False` (the default).

---

## Examples

```bash
# macOS / Linux

# Minimal — only --input-file is required
python run_classification.py -i inputs/polar_data_v4.jsonl

# Custom output directory
python run_classification.py -i inputs/polar_data_v4.jsonl -o output/experiment_1

# Override where the intermediate JSONL is written
python run_classification.py -i inputs/polar_data_v4.jsonl --intermediate-path /tmp/intermediate.jsonl

# Enable tag validation against the model's label set
python run_classification.py -i inputs/polar_data_v4.jsonl --validate-tags

# Fully explicit — all defaults shown explicitly
python run_classification.py \
  --input-file inputs/polar_data_v4.jsonl \
  --model-folder model/20260304 \
  --csv-path inputs/EntityTypesForTokenClassification_with_tags.csv \
  --config config/classification_config.json \
  --output-dir output
```

```powershell
# Windows (PowerShell) — use backtick ` for line continuation

# Fully explicit
python run_classification.py `
  --input-file inputs/polar_data_v4.jsonl `
  --model-folder model/20260304 `
  --csv-path inputs/EntityTypesForTokenClassification_with_tags.csv `
  --config config/classification_config.json `
  --output-dir output
```

---

## Running the Stages Independently

| Stage | Script | Input | Output |
|---|---|---|---|
| 1 — NER Prediction | `model/eval_model/predict_entities_only.py` | `{index, text}` JSONL | `{index, text, entities}` JSONL |
| 2 — Classification | `process_entities_enhanced.py` | `{index, text, entities}` JSONL | CSV + JSON reports |
| **Combined** | **`run_classification.py`** | text string, list of strings, or JSONL file | `ClassificationResult` object (+ optional CSV / JSON files) |

### Stage 1 only — NER prediction

```bash
# Run from model/eval_model/
python predict_entities_only.py --input_file task_instructions_tau2.jsonl --model_folder ../20260304

# With tag validation
python predict_entities_only.py --input_file messages_tau2.jsonl --validate-tags
```

Output goes to `<model_folder>/pred_output/<input_stem>_with_entities.jsonl`.

### Stage 2 only — Classification (requires pre-annotated JSONL)

```bash
python process_entities_enhanced.py -j model/20260304/pred_output/polar_data_v4_with_entities.jsonl
```

---

## Input Format (Stage 1)

Plain JSONL with one record per line, each having `index` and `text`:

```jsonl
{"index": 0, "text": "Patient John Smith was prescribed 500mg ibuprofen on 2024-01-15."}
{"index": 1, "text": "Contact us at support@example.com or call 555-123-4567."}
```

When passing raw strings via the API, this format is created automatically from your input.

## Intermediate Format (between stages)

```jsonl
{"index": 0, "text": "Patient John Smith ...", "entities": [[8, 18, "PN"], [33, 43, "DRUG"], [47, 57, "DATE"]]}
{"index": 1, "text": "Contact us at ...", "entities": [[14, 34, "EMAIL"], [43, 55, "PHONE"]]}
```

Each entity is `[start_char, end_char, TAG]`.

---

## See Also

- [README.md](README.md) — Importable API reference (`run_classification()`)
- [README_LOGIC.md](README_LOGIC.md) — Classification logic, entity rules, identity bundles, and compliance
