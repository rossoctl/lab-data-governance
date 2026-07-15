#!/usr/bin/env python3
"""
End-to-end pipeline: NER prediction → enhanced entity classification.

Step 1 — Runs predict_entities_from_jsonl() to annotate a raw {index, text}
          JSONL file with entity spans, writing an intermediate
          {index, text, entities} JSONL to <model_folder>/pred_output/.

Step 2 — Runs process_jsonl() on the intermediate file to produce the final
          classification results (sensitivity levels, domains, identity bundles).

Importable API
--------------
# From a JSONL file via input_file:
>>> from run_classification import run_classification
>>> result = run_classification(input_file="inputs/polar_data_v4.jsonl")
>>> result.records[0]["summary"]["sensitivity_level"]
>>> import json; json.dumps(result.records, indent=2)

# From a single raw text string (takes precedence over input_file):
>>> result = run_classification(input="John Smith lives at 42 Main St")

# From a list of raw text strings (takes precedence over input_file):
>>> result = run_classification(input=["John Smith lives at 42 Main St",
...                                    "Contact us at support@example.com"])

# Both provided — input wins:
>>> result = run_classification(input="some text", input_file="inputs/polar_data_v4.jsonl")

Pass save_output=True (and optionally output_dir) to also write CSV + JSON files:
>>> result = run_classification(input_file="inputs/polar_data_v4.jsonl",
...                             save_output=True, output_dir="output/custom")
>>> print(result.json_path)

CLI usage  (file only — use the importable API for raw text)
---------
    python run_classification.py --input-file inputs/polar_data_v4.jsonl
    python run_classification.py -i inputs/polar_data_v4.jsonl --model-folder model/20260304
    python run_classification.py -i inputs/polar_data_v4.jsonl -o output/custom --validate-tags
"""

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Union

# Make model/utils importable (mirrors the sys.path trick in predict_entities_only.py)
# model/utils has an __init__.py, so adding model/ to sys.path makes
# `from utils.predict_entities import predict_entities` work inside predict_entities_only.py.
# model/eval_model/ has no __init__.py, so we add it directly for a flat import.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'model')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'model', 'eval_model')))

from predict_entities_only import predict_entities_from_jsonl
from process_entities_enhanced import (
    load_classification_config,
    load_entity_metadata,
    process_jsonl,
    write_output_csv,
    write_output_json,
)


@dataclass
class ClassificationResult:
    """
    Returned by run_classification().

    Attributes
    ----------
    records : List[Dict]
        Rich per-record output (one dict per input row) with full entity
        details and a summary block.  Directly JSON-serialisable:
            json.dumps(result.records, indent=2)

    csv_records : List[Dict]
        Flat, tabular version of the same data (mirrors the CSV columns).

    intermediate_path : Path
        Path to the NER-annotated intermediate JSONL produced in Step 1.

    output_dir : Optional[Path]
        Root output folder used when save_output=True; None otherwise.

    csv_path : Optional[Path]
        Path to the written CSV file, or None if save_output=False.

    json_path : Optional[Path]
        Path to the written JSON file, or None if save_output=False.
    """
    records: List[Dict]
    csv_records: List[Dict]
    intermediate_path: Path
    output_dir: Optional[Path] = None
    csv_path: Optional[Path] = None
    json_path: Optional[Path] = None


def _texts_to_jsonl(texts: List[str]) -> Path:
    """
    Write a list of raw text strings to a temporary JSONL file in
    {index, text} format and return its path.

    The caller is responsible for deleting the file when done (or it will
    be cleaned up when the process exits, as it lives in the system's
    temp directory).
    """
    tmp = tempfile.NamedTemporaryFile(
        mode='w', suffix='.jsonl', delete=False, encoding='utf-8'
    )
    for i, text in enumerate(texts):
        tmp.write(json.dumps({"index": i, "text": text}, ensure_ascii=False) + '\n')
    tmp.close()
    return Path(tmp.name)


def run_classification(
    input: Union[str, List[str], None] = None,
    *,
    input_file: Optional[str] = None,
    model_folder: str = 'model/20260304',
    intermediate_path: Optional[str] = None,
    csv_metadata_path: str = 'inputs/EntityTypesForTokenClassification_with_tags.csv',
    config_path: str = 'config/classification_config.json',
    validate_tags: bool = False,
    save_output: bool = False,
    output_dir: str = 'output',
) -> ClassificationResult:
    """
    Run the full NER → classification pipeline and return a ClassificationResult.

    Parameters
    ----------
    input : str or List[str], optional
        Raw text to classify.  One of:
        - A single raw text string.
        - A list of raw text strings.
        A temporary JSONL file is created automatically and deleted after
        the pipeline finishes.
        Takes precedence over ``input_file`` when both are provided.
    input_file : str, optional
        Path to a raw ``{index, text}`` JSONL file.  Used only when
        ``input`` is None (or not provided).
    model_folder : str
        Path to the NER model directory.
    intermediate_path : str, optional
        Where to write the intermediate {index, text, entities} JSONL.
        Defaults to <model_folder>/pred_output/<input_stem>_with_entities.jsonl.
    csv_metadata_path : str
        Path to entity metadata CSV.
    config_path : str
        Path to classification config JSON.
    validate_tags : bool
        Validate predicted entity tags against the metadata CSV.
    save_output : bool
        When True, write the CSV and JSON result files to disk.
        Defaults to False (in-memory only).
    output_dir : str
        Root output directory used when save_output=True.

    Returns
    -------
    ClassificationResult
        Dataclass with records, csv_records, intermediate_path, and
        (when save_output=True) output_dir, csv_path, json_path.

    Raises
    ------
    ValueError
        If neither ``input`` nor ``input_file`` is provided.
    """
    # ------------------------------------------------------------------ #
    # Resolve input: raw text(s) take precedence over input_file         #
    # ------------------------------------------------------------------ #
    tmp_jsonl: Optional[Path] = None  # track temp file for cleanup
    resolved_input_file: str

    if input is not None:
        # --- raw text path (takes precedence) ---
        if isinstance(input, list):
            tmp_jsonl = _texts_to_jsonl(input)
            resolved_input_file = str(tmp_jsonl)
            print(f"[INFO] {len(input)} text string(s) written to temporary JSONL: {resolved_input_file}")
        else:
            tmp_jsonl = _texts_to_jsonl([input])
            resolved_input_file = str(tmp_jsonl)
            print(f"[INFO] Text string written to temporary JSONL: {resolved_input_file}")
        if input_file is not None:
            print("[INFO] Both 'input' and 'input_file' were provided; 'input' takes precedence.")
    elif input_file is not None:
        # --- file path fallback ---
        resolved_input_file = input_file
    else:
        raise ValueError("Provide either 'input' (text / list of texts) or 'input_file' (JSONL path).")

    try:
        print("=" * 70)
        print("Pipeline: NER Prediction → Enhanced Entity Classification")
        print("=" * 70)
        print(f"\n[INPUT]  JSONL       : {resolved_input_file}")
        print(f"[INPUT]  Model folder: {model_folder}")
        print(f"[INPUT]  CSV metadata: {csv_metadata_path}")
        print(f"[INPUT]  Config      : {config_path}")
        if save_output:
            print(f"[OUTPUT] Directory   : {output_dir}")
        if intermediate_path:
            print(f"[INTER]  Path        : {intermediate_path}")
        print()

        # -------------------------------------------------------------- #
        # Step 1 — NER prediction                                         #
        # -------------------------------------------------------------- #
        print("STEP 1 — Predicting entities")
        print("-" * 70)

        natural_output_path = predict_entities_from_jsonl(
            model_folder=model_folder,
            validate_tags=validate_tags,
            input_path=resolved_input_file,
        )

        # If the caller specified a custom intermediate path, move it there.
        resolved_intermediate = natural_output_path
        if intermediate_path and Path(intermediate_path).resolve() != natural_output_path.resolve():
            import shutil
            Path(intermediate_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(natural_output_path), intermediate_path)
            resolved_intermediate = Path(intermediate_path)
            print(f"[INFO] Intermediate file moved to: {resolved_intermediate}")

        # -------------------------------------------------------------- #
        # Step 2 — Enhanced classification                                #
        # -------------------------------------------------------------- #
        print("\nSTEP 2 — Enhanced entity classification")
        print("-" * 70)

        config = load_classification_config(config_path)
        entity_metadata = load_entity_metadata(csv_metadata_path)
        csv_data, json_data = process_jsonl(str(resolved_intermediate), entity_metadata, config)

        # -------------------------------------------------------------- #
        # Optional file output                                            #
        # -------------------------------------------------------------- #
        out_dir_path: Optional[Path] = None
        csv_out: Optional[Path] = None
        json_out: Optional[Path] = None

        if save_output:
            stem = resolved_intermediate.stem
            timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            out_dir_path = Path(output_dir) / f"{stem}_{timestamp}"

            csv_out = out_dir_path / 'processed_entities_enhanced.csv'
            json_out = out_dir_path / 'processed_entities_detailed.json'

            write_output_csv(csv_data, str(csv_out))
            write_output_json(json_data, str(json_out))

        print("\n[DONE] Pipeline complete!")
        print("=" * 70)
        print(f"  Intermediate JSONL : {resolved_intermediate}")
        if save_output:
            print(f"  Final CSV          : {csv_out}")
            print(f"  Final JSON         : {json_out}")
        print("=" * 70)

        return ClassificationResult(
            records=json_data,
            csv_records=csv_data,
            intermediate_path=resolved_intermediate,
            output_dir=out_dir_path,
            csv_path=csv_out,
            json_path=json_out,
        )

    finally:
        # Clean up the temp JSONL if we created one
        if tmp_jsonl and tmp_jsonl.exists():
            tmp_jsonl.unlink()


def main():
    parser = argparse.ArgumentParser(
        description='End-to-end pipeline: NER prediction → enhanced entity classification',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with all defaults (only --input-file is required)
  python run_classification.py --input-file inputs/polar_data_v4.jsonl

  # Custom model folder and output directory
  python run_classification.py -i inputs/polar_data_v4.jsonl --model-folder model/20260304 -o output/custom

  # Override where the intermediate JSONL lands and enable tag validation
  python run_classification.py -i inputs/polar_data_v4.jsonl --intermediate-path /tmp/intermediate.jsonl --validate-tags
        """
    )

    parser.add_argument('--input-file', '-i', dest='input_file', required=True,
                        help='Path to the raw {index, text} input JSONL file')
    parser.add_argument('--model-folder', dest='model_folder', default='model/20260304',
                        help='Path to the NER model directory (default: model/20260304)')
    parser.add_argument('--intermediate-path', dest='intermediate_path', default=None,
                        help=('Where to write the intermediate {index, text, entities} JSONL. '
                              'Defaults to <model_folder>/pred_output/<input_stem>_with_entities.jsonl'))
    parser.add_argument('--output-dir', '-o', dest='output_dir', default='output',
                        help='Output directory for final CSV and JSON (default: output)')
    parser.add_argument('--csv-path', dest='csv_path',
                        default='inputs/EntityTypesForTokenClassification_with_tags.csv',
                        help='Path to entity metadata CSV')
    parser.add_argument('--config', '-c', dest='config', default='config/classification_config.json',
                        help='Path to classification config JSON (default: config/classification_config.json)')
    parser.add_argument('--validate-tags', dest='validate_tags', action='store_true',
                        help='Validate predicted entity tags against EntityTypesForTokenClassification.csv')

    args = parser.parse_args()

    run_classification(
        input_file=args.input_file,
        model_folder=args.model_folder,
        intermediate_path=args.intermediate_path,
        csv_metadata_path=args.csv_path,
        config_path=args.config,
        validate_tags=args.validate_tags,
        save_output=True,
        output_dir=args.output_dir,
    )


if __name__ == '__main__':
    main()
