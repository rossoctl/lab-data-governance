#!/usr/bin/env python3
"""
Configuration Generator
Generates classification_config.json from EntityTypesForTokenClassification.csv
Uses minimal config approach with CSV-based defaults.
"""

import csv
import json


def generate_classification_config(csv_path='EntityTypesForTokenClassification.csv'):
    """
    Generate classification configuration from CSV file.
    Uses minimal config approach - only special cases are explicitly defined.
    """
    
    # Start with base structure
    config = {
        "entity_classification_mapping": {},
        "default_classification_rules": {
            "by_data_type": {
                "personID": {
                    "classification_level": "RESTRICTED",
                    "identifier_type": "PID",
                    "regulatory_tags": ["PII"],
                    "reason": "Person identifier - strong identifier"
                },
                "operationalID": {
                    "classification_level": "INTERNAL",
                    "identifier_type": "OPID",
                    "regulatory_tags": [],
                    "reason": "Operational identifier - non-personal"
                },
                "data": {
                    "classification_level": "INTERNAL",
                    "identifier_type": "NON_ID",
                    "regulatory_tags": [],
                    "reason": "Data field - non-identifying"
                }
            },
            "by_pi_pii": {
                "PII": {
                    "upgrade_classification": "CONFIDENTIAL",
                    "add_regulatory_tag": "PII",
                    "upgrade_identifier_type": "PID"
                },
                "PI": {
                    "upgrade_classification": None,
                    "add_regulatory_tag": None,
                    "upgrade_identifier_type": None
                }
            }
        },
        "domain_regulatory_mapping": {
            "medical": {
                "pii_entities_get_phi": True,
                "default_sensitivity": "high"
            },
            "finance": {
                "pci_entities": ["PCN", "CVV", "PCED", "PCI", "PCLD"],
                "default_sensitivity": "high"
            },
            "person": {
                "default_sensitivity": "medium"
            }
        },
        "identity_bundle_patterns": [
            {
                "name": "name_dob",
                "entities": ["PN", "DOB"],
                "description": "Person name + Date of birth"
            },
            {
                "name": "name_address",
                "entities": ["PN", "ADDRESS"],
                "description": "Person name + Address"
            },
            {
                "name": "name_phone",
                "entities": ["PN", "PHONE"],
                "description": "Person name + Phone number"
            },
            {
                "name": "name_email",
                "entities": ["PN", "EMAIL"],
                "description": "Person name + Email"
            },
            {
                "name": "name_ssn",
                "entities": ["PN", "SSN"],
                "description": "Person name + SSN"
            },
            {
                "name": "email_phone",
                "entities": ["EMAIL", "PHONE"],
                "description": "Email + Phone"
            },
            {
                "name": "full_financial",
                "entities": ["PN", "BAN", "RN"],
                "description": "Name + Bank account + Routing number"
            },
            {
                "name": "medical_record",
                "entities": ["PN", "MRN"],
                "description": "Name + Medical record number"
            },
            {
                "name": "payment_card_full",
                "entities": ["PN", "PCN"],
                "description": "Name + Payment card number"
            }
        ],
        "classification_hierarchy": [
            "PUBLIC",
            "INTERNAL",
            "CONFIDENTIAL",
            "RESTRICTED"
        ]
    }
    
    # Special cases that need explicit configuration
    # These override the CSV-based defaults
    special_cases = {
        # Payment Card Industry (PCI) entities
        "PCN": {
            "classification_level": "RESTRICTED",
            "identifier_type": "PID",
            "regulatory_tags": ["PII", "PCI"],
            "reason": "Payment card number - PCI-DSS regulated"
        },
        "CVV": {
            "classification_level": "RESTRICTED",
            "identifier_type": "NON_ID",
            "regulatory_tags": ["PCI"],
            "reason": "Card verification value - PCI-DSS regulated"
        },
        "PCED": {
            "classification_level": "RESTRICTED",
            "identifier_type": "NON_ID",
            "regulatory_tags": ["PCI"],
            "reason": "Payment card expiration - PCI-DSS regulated"
        },
        "PCLD": {
            "classification_level": "RESTRICTED",
            "identifier_type": "NON_ID",
            "regulatory_tags": ["PCI"],
            "reason": "Payment card last digits - PCI-DSS regulated"
        },
        
        # Medical identifiers (PHI)
        "MRN": {
            "classification_level": "RESTRICTED",
            "identifier_type": "PID",
            "regulatory_tags": ["PII", "PHI"],
            "reason": "Medical record number - HIPAA protected"
        },
        "HICN": {
            "classification_level": "RESTRICTED",
            "identifier_type": "PID",
            "regulatory_tags": ["PII", "PHI"],
            "reason": "Health insurance claim number - HIPAA protected"
        },
        "HIP": {
            "classification_level": "RESTRICTED",
            "identifier_type": "PID",
            "regulatory_tags": ["PII", "PHI"],
            "reason": "Health insurance policy - HIPAA protected"
        },
        "MED": {
            "classification_level": "RESTRICTED",
            "identifier_type": "PID",
            "regulatory_tags": ["PII", "PHI"],
            "reason": "Medicare/Medicaid number - HIPAA protected"
        },
        
        # Strong identifiers
        "SSN": {
            "classification_level": "RESTRICTED",
            "identifier_type": "PID",
            "regulatory_tags": ["PII"],
            "reason": "Social Security Number - strong identifier"
        },
        "DLN": {
            "classification_level": "RESTRICTED",
            "identifier_type": "PID",
            "regulatory_tags": ["PII"],
            "reason": "Driver's license number - government ID"
        },
        "PPN": {
            "classification_level": "RESTRICTED",
            "identifier_type": "PID",
            "regulatory_tags": ["PII"],
            "reason": "Passport number - government ID"
        },
        "NID": {
            "classification_level": "RESTRICTED",
            "identifier_type": "PID",
            "regulatory_tags": ["PII"],
            "reason": "National ID - government ID"
        },
        
        # Financial identifiers
        "BAN": {
            "classification_level": "RESTRICTED",
            "identifier_type": "PID",
            "regulatory_tags": ["PII"],
            "reason": "Bank account number - financial identifier"
        },
        "IBAN": {
            "classification_level": "RESTRICTED",
            "identifier_type": "PID",
            "regulatory_tags": ["PII"],
            "reason": "International bank account number"
        },
        "RN": {
            "classification_level": "RESTRICTED",
            "identifier_type": "OPID",
            "regulatory_tags": [],
            "reason": "Routing number - financial operational ID"
        },
        
        # Credentials
        "PW": {
            "classification_level": "RESTRICTED",
            "identifier_type": "NON_ID",
            "regulatory_tags": [],
            "reason": "Password - authentication credential"
        },
        "AAD": {
            "classification_level": "RESTRICTED",
            "identifier_type": "NON_ID",
            "regulatory_tags": [],
            "reason": "Authentication data - security credential"
        },
        "UN": {
            "classification_level": "CONFIDENTIAL",
            "identifier_type": "PID",
            "regulatory_tags": ["PII"],
            "reason": "Username - account identifier"
        },
        
        # Biometric
        "DNA": {
            "classification_level": "RESTRICTED",
            "identifier_type": "PID",
            "regulatory_tags": ["PII"],
            "reason": "DNA sequence - biometric data"
        }
    }
    
    config["entity_classification_mapping"] = special_cases
    
    return config


def main():
    """Generate and save configuration file."""
    print("=" * 60)
    print("Classification Config Generator")
    print("=" * 60)
    print("\nGenerating minimal configuration with CSV-based defaults...")
    
    config = generate_classification_config()
    
    output_path = 'config/classification_config.json'
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    
    print(f"\n[OK] Configuration saved to: {output_path}")
    print(f"  Special cases defined: {len(config['entity_classification_mapping'])}")
    print(f"  Identity bundle patterns: {len(config['identity_bundle_patterns'])}")
    print(f"  Domain mappings: {len(config['domain_regulatory_mapping'])}")
    
    print("\n[SUMMARY] Special Cases:")
    for tag, mapping in config['entity_classification_mapping'].items():
        level = mapping['classification_level']
        tags = ', '.join(mapping['regulatory_tags']) if mapping['regulatory_tags'] else 'None'
        print(f"  {tag:10} -> {level:15} | Tags: {tags}")
    
    print("\n[DONE] Configuration generation complete!")
    print("=" * 60)


if __name__ == '__main__':
    main()

# Made with Bob
