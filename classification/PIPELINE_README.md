# End-to-End Pipeline: NER Prediction → Entity Classification

## Overview

`run_pipeline.py` combines two existing scripts into a single end-to-end command. It takes a raw `{index, text}` JSONL file, runs the trained NER model to detect entity spans (Stage 1), then applies the enhanced classification logic to produce sensitivity levels, regulatory tags, and identity bundle detection (Stage 2).

| Stage | Script | Input | Output |
|---|---|---|---|
| 1 — NER Prediction | `model/eval_model/predict_entities_only.py` | `{index, text}` JSONL | `{index, text, entities}` JSONL |
| 2 — Classification | `process_entities_enhanced.py` | `{index, text, entities}` JSONL | CSV + JSON reports |
| **Combined** | **`run_pipeline.py`** | `{index, text}` JSONL | CSV + JSON reports |

For documentation on the classification logic, sensitivity levels, identity bundles, and output formats, see [README.md](README.md).

---

## How It Works

```
inputs/polar_data_v4.jsonl          model/20260304/
  {index, text}                           NER model
         │                                    │
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
     output/<stem>_<timestamp>/
       processed_entities_enhanced.csv
       processed_entities_detailed.json
```

Both stages run in a **single Python process** via library calls (no subprocesses).

The intermediate `{index, text, entities}` JSONL is written to disk by default at `<model_folder>/pred_output/<stem>_with_entities.jsonl`. This preserves Stage 1's built-in **resume checkpoint** — if the run is interrupted, re-running the pipeline will skip already-processed records.

---

## Prerequisites

Install all dependencies via pip (works with pip, conda, or uv):

```bash
# macOS / Linux
pip install -r requirements.txt
```

```powershell
# Windows (PowerShell)
pip install -r requirements.txt
```

If you use **conda**, activate your environment first, then run pip inside it:

```bash
conda activate <your-env-name>
pip install -r requirements.txt
```

> **GPU users:** The default `torch` from PyPI is CPU-only. See the comment in
> `requirements.txt` for instructions on installing a CUDA-enabled build before
> running `pip install -r requirements.txt`.

---

## Quick Start

```powershell
python run_pipeline.py --input-file inputs/polar_data_v4.jsonl
```

The script prints a summary of all output paths at the end:

```
[DONE] Pipeline complete!
======================================================================
  Intermediate JSONL : model/20260304/pred_output/polar_data_v4_with_entities.jsonl
  Final CSV          : output/polar_data_v4_with_entities_2026-07-07_12-36-08/processed_entities_enhanced.csv
  Final JSON         : output/polar_data_v4_with_entities_2026-07-07_12-36-08/processed_entities_detailed.json
======================================================================
```

---

## CLI Reference

```
python run_pipeline.py [OPTIONS]
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

---

## Examples

```bash
# macOS / Linux

# Minimal — only --input-file is required
python run_pipeline.py -i inputs/polar_data_v4.jsonl

# Custom output directory
python run_pipeline.py -i inputs/polar_data_v4.jsonl -o output/experiment_1

# Override where the intermediate JSONL is written
python run_pipeline.py -i inputs/polar_data_v4.jsonl --intermediate-path /tmp/intermediate.jsonl

# Enable tag validation against the model's label set
python run_pipeline.py -i inputs/polar_data_v4.jsonl --validate-tags

# Fully explicit — all defaults shown explicitly
python run_pipeline.py \
  --input-file inputs/polar_data_v4.jsonl \
  --model-folder model/20260304 \
  --csv-path inputs/EntityTypesForTokenClassification_with_tags.csv \
  --config config/classification_config.json \
  --output-dir output
```

```powershell
# Windows (PowerShell) — use backtick ` for line continuation

# Fully explicit
python run_pipeline.py `
  --input-file inputs/polar_data_v4.jsonl `
  --model-folder model/20260304 `
  --csv-path inputs/EntityTypesForTokenClassification_with_tags.csv `
  --config config/classification_config.json `
  --output-dir output
```

---

## Running the Stages Independently

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

See [README.md](README.md) for full Stage 2 documentation.

---

## Input Format (Stage 1)

Plain JSONL with one record per line, each having `index` and `text`:

```jsonl
{"index": 0, "text": "Patient John Smith was prescribed 500mg ibuprofen on 2024-01-15."}
{"index": 1, "text": "Contact us at support@example.com or call 555-123-4567."}
```

## Intermediate Format (between stages)

```jsonl
{"index": 0, "text": "Patient John Smith ...", "entities": [[8, 18, "PN"], [33, 43, "DRUG"], [47, 57, "DATE"]]}
{"index": 1, "text": "Contact us at ...", "entities": [[14, 34, "EMAIL"], [43, 55, "PHONE"]]}
```

Each entity is `[start_char, end_char, TAG]`.

---

## NER Model

- **Base model**: `ibm-granite/granite-embedding-125m-english`
- **Fine-tuned for**: token classification over the project's entity taxonomy
- **Model directory**: `model/20260304/`
- **Supported entity types**: 157 (including `PN`, `DATE`, `EMAIL`, `PHONE`, `SSN`, `DRUG`, `MAMOUNT`, `DIS`, `PROC`, `OPID`, and more)

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

**Last updated:** 2026-07-07  

