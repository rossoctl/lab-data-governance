#!/usr/bin/env python3
"""
Enhanced Entity Processing Script
Processes JSONL file with entity annotations and CSV file with entity metadata
to generate comprehensive CSV and JSON outputs with advanced data classification.

Usage:
    python process_entities_enhanced.py <path_to_csv> <path_to_jsonl>

Example:
    python process_entities_enhanced.py EntityTypesForTokenClassification.csv polar_data_v4.jsonl
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Set


def load_classification_config(config_path: str = 'config/classification_config.json') -> Dict:
    """
    Load classification configuration from JSON file.
    
    Args:
        config_path: Path to the configuration file
        
    Returns:
        Dictionary containing classification rules and mappings
    """
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        print(f"[OK] Loaded classification config from: {config_path}")
        return config
    except FileNotFoundError:
        print(f"[ERROR] Config file not found: {config_path}")
        print("Run 'python generate_config.py' to create it.")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Failed to load config: {e}")
        sys.exit(1)


def load_entity_metadata(csv_path: str) -> Dict[str, Dict[str, str]]:
    """
    Load entity metadata from CSV file.
    
    Args:
        csv_path: Path to the CSV file containing entity types
        
    Returns:
        Dictionary mapping TAG to metadata (Domain, Data Type, Category, regulatory_tags)
    """
    entity_metadata = {}
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            for row in reader:
                tag = row.get('TAG', '').strip()
                domain = row.get('Domain', '').strip()
                data_type = row.get('Data Type', '').strip()
                category = row.get('Category', '').strip()
                regulatory_tags_str = row.get('regulatory_tags', '[]').strip()
                is_personalized_raw = row.get('Is Personalized', '').strip()
                is_personalized = is_personalized_raw == '1'
                
                # Parse regulatory_tags JSON
                try:
                    regulatory_tags = json.loads(regulatory_tags_str)
                except json.JSONDecodeError:
                    regulatory_tags = []
                
                if tag:
                    entity_metadata[tag] = {
                        'domain': domain,
                        'data_type': data_type,
                        'category': category,
                        'regulatory_tags': regulatory_tags,
                        'is_personalized': is_personalized
                    }
        
        print(f"[OK] Loaded {len(entity_metadata)} entity types from CSV")
        return entity_metadata
        
    except FileNotFoundError:
        print(f"[ERROR] CSV file not found: {csv_path}")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Reading CSV file: {e}")
        sys.exit(1)


def get_base_classification(tag: str, entity_metadata: Dict, config: Dict) -> Dict:
    """
    Get base classification for an entity.
    Uses CSV metadata and infers identifier_type from Data Type.
    
    Args:
        tag: Entity TAG
        entity_metadata: Metadata from CSV
        config: Classification configuration
        
    Returns:
        Dictionary with sensitivity_level, identifier_type, regulatory_tags, reason
    """
    if tag not in entity_metadata:
        print(f"  [WARN] TAG '{tag}' not found in CSV, using defaults")
        return {
            "sensitivity_level": "INTERNAL",
            "identifier_type": "NON_ID",
            "regulatory_tags": [],
            "reason": "Unknown entity - default classification"
        }
    
    metadata = entity_metadata[tag]
    data_type = metadata['data_type']
    regulatory_tags = metadata['regulatory_tags']
    
    # Infer identifier_type from Data Type
    if data_type == "personID":
        identifier_type = "PID"
    elif data_type == "operationalID":
        identifier_type = "OPID"
    else:  # data
        identifier_type = "DATA"
    
    # Determine sensitivity level based on regulatory tags
    if "CREDENTIALS" in regulatory_tags or "PHI" in regulatory_tags or "PCI" in regulatory_tags:
        sensitivity_level = "RESTRICTED"
    elif identifier_type == "PID":
        sensitivity_level = "RESTRICTED"
    elif "PII" in regulatory_tags:
        sensitivity_level = "CONFIDENTIAL"
    elif "PI" in regulatory_tags:
        sensitivity_level = "INTERNAL"
    elif identifier_type == "OPID":
        sensitivity_level = "INTERNAL"
    else:
        sensitivity_level = "PUBLIC" #FIXME
    
    return {
        "sensitivity_level": sensitivity_level,
        "identifier_type": identifier_type,
        "regulatory_tags": regulatory_tags.copy(),
        "reason": f"Data type: {data_type}"
    }


def calculate_entity_sensitivity_level(regulatory_tags: List[str], identifier_type: str) -> str:
    """
    Calculate sensitivity level for an individual entity based on its regulatory tags and identifier type.
    
    Args:
        regulatory_tags: List of regulatory tags for the entity
        identifier_type: Type of identifier (PID, OPID, DATA, etc.)
        
    Returns:
        Sensitivity level string (RESTRICTED, CONFIDENTIAL, INTERNAL, PUBLIC)
    """
    # RESTRICTED: High-risk data
    if "CREDENTIALS" in regulatory_tags or "PHI" in regulatory_tags or "PCI" in regulatory_tags:
        return "RESTRICTED"
    if identifier_type == "PID":
        return "RESTRICTED"
    
    # CONFIDENTIAL: PII data
    if "PII" in regulatory_tags:
        return "CONFIDENTIAL"
    
    # INTERNAL: PI or operational identifiers
    if "PI" in regulatory_tags:
        return "INTERNAL"
    if identifier_type == "OPID":
        return "INTERNAL"
    
    # PUBLIC: Everything else
    return "PUBLIC"


def enhance_entity(entity: List, text: str, entity_metadata: Dict, config: Dict) -> Dict:
    """
    Enhance entity with classification attributes.
    
    Args:
        entity: [start, end, TAG] from JSONL
        text: Full text to extract entity text
        entity_metadata: Metadata from CSV
        config: Classification configuration
        
    Returns:
        Enhanced entity object with all classification attributes
    """
    start, end, tag = entity[0], entity[1], entity[2]
    confidence = entity[3] if len(entity) > 3 else 1.0
    
    # Get base classification
    classification = get_base_classification(tag, entity_metadata, config)
    
    # Get domain and category from CSV
    domain = entity_metadata.get(tag, {}).get('domain', 'unknown')
    category = entity_metadata.get(tag, {}).get('category', '')
    is_personalized = entity_metadata.get(tag, {}).get('is_personalized', False)
    
    # Calculate entity-level sensitivity
    entity_sensitivity = calculate_entity_sensitivity_level(
        classification["regulatory_tags"],
        classification["identifier_type"]
    )
    
    # Create enhanced entity object
    entity_obj = {
        "entity_type": tag,
        "start": start,
        "end": end,
        "text": text[start:end] if start < len(text) and end <= len(text) else "",
        "domain": domain,
        "category": category,
        "regulatory_tags": classification["regulatory_tags"].copy(),
        "identifier_type": classification["identifier_type"],
        "sensitivity_level": entity_sensitivity,
        "confidence": confidence,
        "is_personalized": is_personalized
    }
    
    return entity_obj


def detect_identity_bundles(entities: List[Dict], config: Dict) -> List[Dict]:
    """
    Detect identity bundle patterns in entities.
    
    Args:
        entities: List of enhanced entity objects
        config: Classification configuration
        
    Returns:
        List of detected bundles with metadata
    """
    entity_types = set([e["entity_type"] for e in entities])
    detected_bundles = []
    
    for pattern in config["identity_bundle_patterns"]:
        required_entities = set(pattern["entities"])
        if required_entities.issubset(entity_types):
            detected_bundles.append({
                "name": pattern["name"],
                "description": pattern["description"],
                "entities": pattern["entities"]
            })
    
    return detected_bundles


def aggregate_domains(entities: List[Dict]) -> Tuple[List[str], str]:
    """
    Aggregate domains from entities.
    Primary domain prioritizes medical and finance (by max count), then person.
    
    Args:
        entities: List of enhanced entity objects
        
    Returns:
        Tuple of (domains list, primary_domain)
    """
    if not entities:
        return [], ""
    
    domain_counts = {}
    for entity in entities:
        domain = entity["domain"]
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
    
    domains = sorted(list(domain_counts.keys()))
    
    # Prioritize medical and finance over person
    priority_domains = []
    if "medical" in domain_counts:
        priority_domains.append(("medical", domain_counts["medical"]))
    if "finance" in domain_counts:
        priority_domains.append(("finance", domain_counts["finance"]))
    
    if priority_domains:
        # Choose max between medical and finance
        primary_domain = max(priority_domains, key=lambda x: x[1])[0]
    elif "person" in domain_counts:
        primary_domain = "person"
    else:
        # Fallback to most common domain
        primary_domain = max(domain_counts.items(), key=lambda x: x[1])[0]
    
    return domains, primary_domain


def aggregate_regulatory_tags(entities: List[Dict]) -> List[str]:
    """
    Collect all unique regulatory tags from entities.
    
    Args:
        entities: List of enhanced entity objects
        
    Returns:
        Sorted list of unique regulatory tags
    """
    all_tags = set()
    for entity in entities:
        all_tags.update(entity["regulatory_tags"])
    
    return sorted(list(all_tags))


def aggregate_document_classification(entities: List[Dict], identity_bundles: List[Dict],
                                      config: Dict) -> Tuple[str, List[str]]:
    """
    Aggregate entity-level classifications to document level.
    Uses max sensitivity level from entities and identity bundles.
    
    Args:
        entities: List of enhanced entity objects
        identity_bundles: List of detected identity bundles
        config: Classification configuration
        
    Returns:
        Tuple of (sensitivity_level, reasons)
    """
    if not entities:
        return "PUBLIC", ["No entities detected"]
    
    hierarchy = config["classification_hierarchy"]
    reasons = []
    
    # Get max entity sensitivity level
    max_level = max([e["sensitivity_level"] for e in entities],
                    key=lambda x: hierarchy.index(x))
    
    # Find entities with max sensitivity level
    max_level_entities = [e["entity_type"] for e in entities
                          if e["sensitivity_level"] == max_level]
    max_level_entities_unique = sorted(list(set(max_level_entities)))
    
    # Identity bundles force RESTRICTED
    if identity_bundles:
        max_level = "RESTRICTED"
        bundle_names = ', '.join([b['name'] for b in identity_bundles])
        reasons.append(f"Identity bundle detected: {bundle_names}")
    
    # Add reason for max level entities
    if max_level_entities_unique:
        entity_list = ', '.join(max_level_entities_unique)
        reasons.append(f"{max_level} entities: {entity_list}")
    
    return max_level, reasons


def create_summary(entities: List[Dict], identity_bundles: List[Dict], config: Dict) -> Dict:
    """
    Create final classification summary for the document.
    
    Args:
        entities: List of enhanced entity objects
        identity_bundles: List of detected identity bundles
        config: Classification configuration
        
    Returns:
        Complete summary object
    """
    if not entities:
        return {
            "sensitivity_level": "PUBLIC",
            "domains": [],
            "primary_domain": "",
            "regulatory_tags": [],
            "entities": [],
            "identifier_types_present": [],
            "contains_identity_bundle": False,
            "is_personalized": False,
            "personalized_entity_types": [],
            "reasons": ["No entities detected"]
        }
    
    # Aggregate domains
    domains, primary_domain = aggregate_domains(entities)
    
    # Aggregate regulatory tags
    regulatory_tags = aggregate_regulatory_tags(entities)
    
    # Get entity types and identifier types
    entity_types = sorted(list(set([e["entity_type"] for e in entities])))
    identifier_types = sorted(list(set([e["identifier_type"] for e in entities])))
    
    # Aggregate classification
    sensitivity_level, reasons = aggregate_document_classification(
        entities, identity_bundles, config
    )
    
    # Determine flags
    contains_identity_bundle = len(identity_bundles) > 0

    # Personalization flags
    personalized_entity_types = sorted(set(
        e["entity_type"] for e in entities if e.get("is_personalized", False)
    ))
    is_personalized = len(personalized_entity_types) > 0
    
    # Ensure we have at least one reason
    if not reasons:
        reasons.append(f"Sensitivity level: {sensitivity_level}")
    
    return {
        "sensitivity_level": sensitivity_level,
        "domains": domains,
        "primary_domain": primary_domain,
        "regulatory_tags": regulatory_tags,
        "entities": entity_types,
        "identifier_types_present": identifier_types,
        "contains_identity_bundle": contains_identity_bundle,
        "is_personalized": is_personalized,
        "personalized_entity_types": personalized_entity_types,
        "reasons": reasons
    }


def get_unique_domains(tags: List[str], entity_metadata: Dict[str, Dict[str, str]]) -> List[str]:
    """
    Get unique domains for the given tags (OLD format for backward compatibility).
    
    Args:
        tags: List of entity TAGs
        entity_metadata: Dictionary of entity metadata
        
    Returns:
        List of unique domains (sorted)
    """
    domains = set()
    
    for tag in tags:
        if tag in entity_metadata:
            domain = entity_metadata[tag]['domain']
            if domain:
                domains.add(domain)
        else:
            domains.add("UNKNOWN")
    
    return sorted(list(domains))


def process_jsonl(jsonl_path: str, entity_metadata: Dict, config: Dict) -> Tuple[List[Dict], List[Dict]]:
    """
    Process JSONL file and generate output data with enhanced classification.
    
    Args:
        jsonl_path: Path to the JSONL file
        entity_metadata: Dictionary of entity metadata
        config: Classification configuration
        
    Returns:
        Tuple of (csv_output_data, json_output_data)
    """
    csv_output_data = []
    json_output_data = []
    
    try:
        with open(jsonl_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                try:
                    data = json.loads(line.strip())
                    
                    # Preserve index from input (if available)
                    index = data.get('index', line_num - 1)
                    text = data.get('text', '')
                    entities_raw = data.get('entities', [])
                    
                    # Enhance all entities
                    enhanced_entities = []
                    for entity in entities_raw:
                        enhanced = enhance_entity(entity, text, entity_metadata, config)
                        enhanced_entities.append(enhanced)
                    
                    # Detect identity bundles
                    identity_bundles = detect_identity_bundles(enhanced_entities, config)
                    
                    # Create summary
                    summary = create_summary(enhanced_entities, identity_bundles, config)
                    
                    # For backward compatibility - OLD format
                    list_of_entities = [entity[2] for entity in entities_raw]
                    unique_tags = list(dict.fromkeys(list_of_entities))
                    list_of_domains = get_unique_domains(unique_tags, entity_metadata)
                    
                    # CSV output (enhanced with new columns)
                    # Add entity text to entities: [start, end, TAG, text]
                    entities_with_text = [
                        [e[0], e[1], e[2], text[e[0]:e[1]] if e[0] < len(text) and e[1] <= len(text) else ""]
                        for e in entities_raw
                    ]
                    
                    csv_output_data.append({
                        'index': index,
                        'text': text,
                        'entities': json.dumps(entities_with_text),  # Enhanced format with text
                        'list_of_entities': json.dumps(list_of_entities),  # OLD format
                        'list_of_domains': json.dumps(list_of_domains),  # OLD format
                        'sensitivity_level': summary['sensitivity_level'],
                        'primary_domain': summary['primary_domain'] or '',
                        'regulatory_tags': json.dumps(summary['regulatory_tags']),
                        'contains_identity_bundle': summary['contains_identity_bundle'],
                        'identity_bundles': json.dumps([b['name'] for b in identity_bundles]),
                        'is_personalized': summary['is_personalized']
                    })
                    
                    # JSON output (detailed)
                    json_output_data.append({
                        'index': index,
                        'text': text,
                        'entities': enhanced_entities,
                        'summary': summary
                    })
                    
                except json.JSONDecodeError as e:
                    print(f"  [WARN] Invalid JSON on line {line_num}: {e}")
                    continue
        
        print(f"[OK] Processed {len(csv_output_data)} records from JSONL")
        return csv_output_data, json_output_data
        
    except FileNotFoundError:
        print(f"[ERROR] JSONL file not found: {jsonl_path}")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Reading JSONL file: {e}")
        sys.exit(1)


def write_output_csv(output_data: List[Dict], output_path: str):
    """
    Write processed data to enhanced CSV file.
    
    Args:
        output_data: List of dictionaries containing processed data
        output_path: Path to the output CSV file
    """
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        fieldnames = [
            'index', 'text', 'entities', 'list_of_entities', 'list_of_domains',
            'sensitivity_level', 'primary_domain', 'regulatory_tags',
            'contains_identity_bundle', 'identity_bundles', 'is_personalized'
        ]
        
        with open(output_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(output_data)
        
        print(f"[OK] CSV output written to: {output_path}")
        print(f"  Total rows: {len(output_data)}")
        
        # Print sensitivity distribution
        sensitivity_counts = {}
        for row in output_data:
            level = row['sensitivity_level']
            sensitivity_counts[level] = sensitivity_counts.get(level, 0) + 1
        
        print("\n[SUMMARY] Sensitivity Distribution:")
        for level in ['PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED']:
            count = sensitivity_counts.get(level, 0)
            percentage = (count / len(output_data) * 100) if output_data else 0
            print(f"  {level:15} {count:4} ({percentage:5.1f}%)")
        
    except Exception as e:
        print(f"[ERROR] Writing output CSV: {e}")
        sys.exit(1)


def write_output_json(output_data: List[Dict], output_path: str):
    """
    Write processed data to detailed JSON file.
    
    Args:
        output_data: List of dictionaries containing detailed data
        output_path: Path to the output JSON file
    """
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
        
        print(f"[OK] JSON output written to: {output_path}")
        print(f"  Total records: {len(output_data)}")
        
    except Exception as e:
        print(f"[ERROR] Writing output JSON: {e}")
        sys.exit(1)


def main():
    """Main function to orchestrate the enhanced processing."""
    parser = argparse.ArgumentParser(
        description='Process entity annotations with advanced data classification',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python process_entities_enhanced.py --jsonl-path polar_data_v4.jsonl
  python process_entities_enhanced.py -j polar_data_v4.jsonl
  python process_entities_enhanced.py --csv-path data/entities.csv --jsonl-path data/annotations.jsonl -o output/custom
        """
    )
    
    parser.add_argument('--csv-path', '--csv',
                       dest='csv_path',
                       default='inputs/EntityTypesForTokenClassification_with_tags.csv',
                       help='Path to CSV file with entity metadata (default: inputs/EntityTypesForTokenClassification_with_tags.csv)')
    parser.add_argument('--jsonl-path', '-j',
                       dest='jsonl_path',
                       required=True,
                       help='Path to JSONL file with entity annotations')
    parser.add_argument('-o', '--output-dir',
                       help='Output directory (default: output)',
                       default='output')
    parser.add_argument('-c', '--config',
                       help='Path to classification config (default: config/classification_config.json)',
                       default='config/classification_config.json')
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("Enhanced Entity Processing Script")
    print("=" * 70)
    print(f"\n[INPUT] CSV: {args.csv_path}")
    print(f"[INPUT] JSONL: {args.jsonl_path}")
    print(f"[INPUT] Config: {args.config}")
    print(f"[OUTPUT] Directory: {args.output_dir}\n")
    
    # Load classification config
    config = load_classification_config(args.config)
    
    # Load entity metadata from CSV
    entity_metadata = load_entity_metadata(args.csv_path)
    
    # Process JSONL file
    csv_data, json_data = process_jsonl(args.jsonl_path, entity_metadata, config)
    
    # Create timestamped output folder
    jsonl_filename = Path(args.jsonl_path).stem  # Get filename without extension
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    output_folder_name = f"{jsonl_filename}_{timestamp}"
    output_folder_path = os.path.join(args.output_dir, output_folder_name)
    
    # Write outputs to timestamped folder
    csv_output_path = os.path.join(output_folder_path, 'processed_entities_enhanced.csv')
    json_output_path = os.path.join(output_folder_path, 'processed_entities_detailed.json')
    
    write_output_csv(csv_data, csv_output_path)
    write_output_json(json_data, json_output_path)
    
    print("\n[DONE] Processing complete!")
    print("=" * 70)


if __name__ == '__main__':
    main()

# Made with Bob
