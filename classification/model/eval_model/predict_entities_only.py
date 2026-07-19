#!/usr/bin/env python3
"""
Script to predict entities from a JSONL file containing only 'index' and 'text' fields.
Uses a trained token classification model to add entity annotations.

Input format:  {"index": 0, "text": "Some text here"}
Output format: {"index": 0, "text": "Some text here", "entities": [[start, end, "TAG"], ...]}
"""

import sys
import os
import json
import argparse
import csv
from pathlib import Path

# Insert the project root (one level up) into sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import torch
from tqdm import tqdm
from transformers import AutoTokenizer, AutoModelForTokenClassification
from utils.predict_entities import predict_entities, get_optimal_device


def load_expected_tags(csv_path):
    """Load expected entity tags from CSV file."""
    expected_tags = set()
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if 'TAG' in row and row['TAG']:
                    expected_tags.add(row['TAG'].strip())
        return expected_tags
    except Exception as e:
        print(f"Warning: Could not load expected tags from {csv_path}: {e}")
        return None


def validate_output(input_data, output_data, expected_tags=None):
    """
    Validate that output preserves input data and has correct format.
    
    Returns:
        tuple: (is_valid, error_messages)
    """
    errors = []
    
    # Check same number of records
    if len(input_data) != len(output_data):
        errors.append(f"Record count mismatch: input has {len(input_data)}, output has {len(output_data)}")
        return False, errors
    
    # Check each record
    for i, (inp, out) in enumerate(zip(input_data, output_data)):
        # Check index preservation
        if inp.get('index') != out.get('index'):
            errors.append(f"Line {i}: Index mismatch - input: {inp.get('index')}, output: {out.get('index')}")
        
        # Check text preservation
        if inp.get('text') != out.get('text'):
            errors.append(f"Line {i}: Text changed for index {inp.get('index')}")
        
        # Check entities field exists
        if 'entities' not in out:
            errors.append(f"Line {i}: Missing 'entities' field for index {inp.get('index')}")
            continue
        
        # Check entities format
        entities = out.get('entities', [])
        if not isinstance(entities, list):
            errors.append(f"Line {i}: 'entities' must be a list for index {inp.get('index')}")
            continue
        
        # Validate each entity
        text = out.get('text', '')
        for j, entity in enumerate(entities):
            if not isinstance(entity, list) or len(entity) != 3:
                errors.append(f"Line {i}, Entity {j}: Invalid format (must be [start, end, tag]) for index {inp.get('index')}")
                continue
            
            start, end, tag = entity
            
            # Check positions are valid
            if not isinstance(start, int) or not isinstance(end, int):
                errors.append(f"Line {i}, Entity {j}: start/end must be integers for index {inp.get('index')}")
            elif start < 0 or end > len(text) or start >= end:
                errors.append(f"Line {i}, Entity {j}: Invalid positions [{start}, {end}] for text length {len(text)}, index {inp.get('index')}")
            
            # Check tag is string
            if not isinstance(tag, str):
                errors.append(f"Line {i}, Entity {j}: tag must be a string for index {inp.get('index')}")
            
            # Check tag against expected tags if provided
            if expected_tags and tag not in expected_tags:
                errors.append(f"Line {i}, Entity {j}: Unexpected tag '{tag}' for index {inp.get('index')}")
    
    return len(errors) == 0, errors


def predict_entities_from_jsonl(model_folder, input_file=None, validate_tags=False, resume=True, input_path=None):
    """
    Main function to predict entities from JSONL file.
    
    Args:
        model_folder: Path to the model directory
        input_file: Name of the input JSONL file (resolved relative to model_folder)
        validate_tags: Whether to validate predicted tags against expected types
        resume: Whether to resume from existing output file if it exists
        input_path: Full path to the input JSONL file. When provided, overrides
                    model_folder / input_file for reading. The intermediate output
                    still goes to <model_folder>/pred_output/.
    """
    # Setup paths
    model_folder = Path(model_folder)

    if input_path is not None:
        input_path = Path(input_path)
        input_stem = input_path.stem
    else:
        if input_file is None:
            raise ValueError("Either input_file or input_path must be provided")
        input_path = model_folder / input_file
        input_stem = Path(input_file).stem

    # Create output directory
    output_dir = model_folder / "pred_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate output filename
    output_file = f"{input_stem}_with_entities.jsonl"
    output_path = output_dir / output_file
    
    # Check for existing output and resume if requested
    processed_indices = set()
    if resume and output_path.exists():
        print(f"\n⚠️  Found existing output file: {output_path}")
        try:
            with open(output_path, 'r', encoding='utf-8') as f:
                for line in f:
                    record = json.loads(line.strip())
                    processed_indices.add(record.get('index'))
            if processed_indices:
                print(f"✅ Resuming from checkpoint: {len(processed_indices)} records already processed")
        except Exception as e:
            print(f"⚠️  Could not read existing output: {e}")
            print("Starting fresh...")
            processed_indices = set()
    
    # Check if input file exists
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    
    print(f"Input file: {input_path}")
    print(f"Output file: {output_path}")
    print(f"Model folder: {model_folder}")
    
    # Load expected tags if validation is requested
    expected_tags = None
    if validate_tags:
        tags_csv = model_folder / "EntityTypesForTokenClassification.csv"
        if tags_csv.exists():
            expected_tags = load_expected_tags(tags_csv)
            if expected_tags:
                print(f"Loaded {len(expected_tags)} expected entity tags for validation")
        else:
            print(f"Warning: Tag validation requested but {tags_csv} not found")
    
    # Load model and tokenizer
    print("\nLoading model and tokenizer...")
    base_model_name = "ibm-granite/granite-embedding-125m-english"
    
    try:
        tokenizer = AutoTokenizer.from_pretrained(base_model_name)
        device, is_cuda = get_optimal_device()
        model = AutoModelForTokenClassification.from_pretrained(str(model_folder)).to(device)
        id2label = model.config.id2label
        print(f"Model loaded successfully on {device}")
        print(f"Model supports {len(id2label)} entity types")
    except Exception as e:
        raise RuntimeError(f"Failed to load model from {model_folder}: {e}")
    
    # Read input data
    print("\nReading input data...")
    input_data = []
    with open(input_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            try:
                record = json.loads(line.strip())
                if 'index' not in record or 'text' not in record:
                    print(f"Warning: Line {line_num} missing 'index' or 'text' field, skipping")
                    continue
                input_data.append(record)
            except json.JSONDecodeError as e:
                print(f"Warning: Line {line_num} is not valid JSON: {e}")
    
    print(f"Loaded {len(input_data)} records from input file")
    
    if len(input_data) == 0:
        raise ValueError("No valid records found in input file")
    
    # Predict entities and write incrementally to avoid memory issues
    print("\nPredicting entities...")
    print(f"Writing output incrementally to {output_path}...")
    
    output_data = []  # Keep for validation
    total_entities = 0
    records_with_entities = 0
    entity_types = {}
    
    # Determine file mode based on resume
    file_mode = 'a' if processed_indices else 'w'
    records_to_process = [r for r in input_data if r['index'] not in processed_indices]
    
    if processed_indices:
        print(f"Skipping {len(processed_indices)} already processed records")
        print(f"Processing remaining {len(records_to_process)} records...")
    
    with open(output_path, file_mode, encoding='utf-8') as out_file:
        for record in tqdm(records_to_process, desc="Processing", unit="record"):
            index = record['index']
            text = record['text']
            
            # Predict entities
            try:
                predicted_entities = predict_entities(text, model, tokenizer, id2label, device, is_cuda)
                
                # Convert to required format: [[start, end, "TAG"], ...]
                entities = [
                    [ent['start'], ent['end'], ent['label']]
                    for ent in predicted_entities
                ]
                
                # Track statistics
                if entities:
                    records_with_entities += 1
                    total_entities += len(entities)
                    for _, _, tag in entities:
                        entity_types[tag] = entity_types.get(tag, 0) + 1
                
                # Create output record
                output_record = {
                    'index': index,
                    'text': text,
                    'entities': entities
                }
                
                # Write immediately to file
                out_file.write(json.dumps(output_record, ensure_ascii=False) + '\n')
                out_file.flush()  # Ensure data is written to disk
                
                # Keep in memory for validation (only store minimal info)
                output_data.append(output_record)
                
            except Exception as e:
                print(f"\nError processing index {index}: {e}")
                # Add empty entities on error to maintain alignment
                error_record = {
                    'index': index,
                    'text': text,
                    'entities': []
                }
                out_file.write(json.dumps(error_record, ensure_ascii=False) + '\n')
                out_file.flush()
                output_data.append(error_record)
    
    # If we resumed, read the full output for validation
    if processed_indices:
        print("\nReading complete output file for validation...")
        output_data = []
        with open(output_path, 'r', encoding='utf-8') as f:
            for line in f:
                output_data.append(json.loads(line.strip()))
    
    # Validate output
    print("\nValidating output...")
    is_valid, errors = validate_output(input_data, output_data, expected_tags)
    
    if not is_valid:
        print(f"\n⚠️  Validation found {len(errors)} issue(s):")
        for error in errors[:10]:  # Show first 10 errors
            print(f"  - {error}")
        if len(errors) > 10:
            print(f"  ... and {len(errors) - 10} more errors")
        print("\nOutput already saved (written incrementally during processing).")
    else:
        print("✅ Validation passed!")
    
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"Total records processed: {len(output_data)}")
    print(f"Records with entities: {records_with_entities}")
    print(f"Records without entities: {len(output_data) - records_with_entities}")
    print(f"Total entities predicted: {total_entities}")
    if len(output_data) > 0:
        print(f"Average entities per record: {total_entities / len(output_data):.2f}")
    
    # Entity type distribution
    if total_entities > 0:
        print(f"\nEntity type distribution (top 10):")
        for tag, count in sorted(entity_types.items(), key=lambda x: x[1], reverse=True)[:10]:
            print(f"  {tag}: {count}")
    
    print("="*60)
    print(f"\n✅ Output saved to: {output_path}")
    
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Predict entities from JSONL file with index and text fields",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Use defaults (model: 20260304, input: task_instructions_tau2.jsonl)
  python predict_entities_only.py
  
  # Specify model folder
  python predict_entities_only.py --model_folder 20260304
  
  # Specify custom input file
  python predict_entities_only.py --input_file custom_input.jsonl
  
  # Specify both
  python predict_entities_only.py --model_folder 20260304 --input_file messages_tau2.jsonl
  
  # With tag validation
  python predict_entities_only.py --model_folder 20260304 --input_file messages_tau2.jsonl --validate-tags
        """
    )
    
    parser.add_argument(
        '--model_folder',
        default='20260304',
        help='Path to the model directory (default: 20260304)'
    )
    
    parser.add_argument(
        '--input_file',
        default='task_instructions_tau2.jsonl',
        help='Input JSONL filename (default: task_instructions_tau2.jsonl)'
    )
    
    parser.add_argument(
        '--validate-tags',
        action='store_true',
        help='Validate predicted entity tags against EntityTypesForTokenClassification.csv'
    )
    
    args = parser.parse_args()
    
    try:
        predict_entities_from_jsonl(
            args.model_folder,
            args.input_file,
            args.validate_tags
        )
    except Exception as e:
        print(f"\n❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

# Made with Bob
