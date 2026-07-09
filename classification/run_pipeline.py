#!/usr/bin/env python3
"""
End-to-end pipeline: NER prediction → enhanced entity classification.

Step 1 — Runs predict_entities_from_jsonl() to annotate a raw {index, text}
          JSONL file with entity spans, writing an intermediate
          {index, text, entities} JSONL to <model_folder>/pred_output/.

Step 2 — Runs process_jsonl() on the intermediate file to produce the final
          CSV and JSON outputs (sensitivity levels, domains, identity bundles).

Usage:
    python run_pipeline.py --input-file inputs/polar_data_v4.jsonl
    python run_pipeline.py -i inputs/polar_data_v4.jsonl --model-folder model/20260304
    python run_pipeline.py -i inputs/polar_data_v4.jsonl -o output/custom --validate-tags
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

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


def main():
    parser = argparse.ArgumentParser(
        description='End-to-end pipeline: NER prediction → enhanced entity classification',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with all defaults (only --input-file is required)
  python run_pipeline.py --input-file inputs/polar_data_v4.jsonl

  # Custom model folder and output directory
  python run_pipeline.py -i inputs/polar_data_v4.jsonl --model-folder model/20260304 -o output/custom

  # Override where the intermediate JSONL lands and enable tag validation
  python run_pipeline.py -i inputs/polar_data_v4.jsonl --intermediate-path /tmp/intermediate.jsonl --validate-tags
        """
    )

    parser.add_argument(
        '--input-file', '-i',
        dest='input_file',
        required=True,
        help='Path to the raw {index, text} input JSONL file',
    )
    parser.add_argument(
        '--model-folder',
        dest='model_folder',
        default='model/20260304',
        help='Path to the NER model directory (default: model/20260304)',
    )
    parser.add_argument(
        '--intermediate-path',
        dest='intermediate_path',
        default=None,
        help=(
            'Where to write the intermediate {index, text, entities} JSONL. '
            'Defaults to <model_folder>/pred_output/<input_stem>_with_entities.jsonl'
        ),
    )
    parser.add_argument(
        '--output-dir', '-o',
        dest='output_dir',
        default='output',
        help='Output directory for final CSV and JSON (default: output)',
    )
    parser.add_argument(
        '--csv-path',
        dest='csv_path',
        default='inputs/EntityTypesForTokenClassification_with_tags.csv',
        help='Path to entity metadata CSV (default: inputs/EntityTypesForTokenClassification_with_tags.csv)',
    )
    parser.add_argument(
        '--config', '-c',
        dest='config',
        default='config/classification_config.json',
        help='Path to classification config JSON (default: config/classification_config.json)',
    )
    parser.add_argument(
        '--validate-tags',
        dest='validate_tags',
        action='store_true',
        help='Validate predicted entity tags against EntityTypesForTokenClassification.csv',
    )

    args = parser.parse_args()

    print("=" * 70)
    print("Pipeline: NER Prediction → Enhanced Entity Classification")
    print("=" * 70)
    print(f"\n[INPUT]  JSONL       : {args.input_file}")
    print(f"[INPUT]  Model folder: {args.model_folder}")
    print(f"[INPUT]  CSV metadata: {args.csv_path}")
    print(f"[INPUT]  Config      : {args.config}")
    print(f"[OUTPUT] Directory   : {args.output_dir}")
    if args.intermediate_path:
        print(f"[INTER]  Path        : {args.intermediate_path}")
    print()

    # ------------------------------------------------------------------ #
    # Step 1 — NER prediction                                             #
    # ------------------------------------------------------------------ #
    print("STEP 1 — Predicting entities")
    print("-" * 70)

    natural_output_path = predict_entities_from_jsonl(
        model_folder=args.model_folder,
        validate_tags=args.validate_tags,
        input_path=args.input_file,
    )

    # If the user specified a custom intermediate path, move it there.
    intermediate_path = natural_output_path
    if args.intermediate_path and Path(args.intermediate_path).resolve() != natural_output_path.resolve():
        import shutil
        Path(args.intermediate_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(natural_output_path), args.intermediate_path)
        intermediate_path = Path(args.intermediate_path)
        print(f"[INFO] Intermediate file moved to: {intermediate_path}")

    # ------------------------------------------------------------------ #
    # Step 2 — Enhanced classification                                    #
    # ------------------------------------------------------------------ #
    print("\nSTEP 2 — Enhanced entity classification")
    print("-" * 70)

    config = load_classification_config(args.config)
    entity_metadata = load_entity_metadata(args.csv_path)
    csv_data, json_data = process_jsonl(str(intermediate_path), entity_metadata, config)

    # Build timestamped output folder (same convention as process_entities_enhanced.py)
    stem = intermediate_path.stem
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    output_folder = Path(args.output_dir) / f"{stem}_{timestamp}"

    write_output_csv(csv_data, str(output_folder / 'processed_entities_enhanced.csv'))
    write_output_json(json_data, str(output_folder / 'processed_entities_detailed.json'))

    print("\n[DONE] Pipeline complete!")
    print("=" * 70)
    print(f"  Intermediate JSONL : {intermediate_path}")
    print(f"  Final CSV          : {output_folder / 'processed_entities_enhanced.csv'}")
    print(f"  Final JSON         : {output_folder / 'processed_entities_detailed.json'}")
    print("=" * 70)


if __name__ == '__main__':
    main()


