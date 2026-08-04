# Recommended Enum Values for Policy Schema

This document provides comprehensive enum values for all fields in the policy schema, based on:
- Use cases in [`final_examples.md`](final_examples.md:1)
- Entity Classification System (78 entity types)
- Common data governance and security patterns
- OWASP Agentic AI Threats

---

## 1. Entity Types (78 types from EntityTypesForTokenClassification.csv)

**Source:** EntityTypesForTokenClassification (8).csv

### Person Domain (38 types)
```json
"entity_type_person": [
    "PN",           // Person Name
    "SSN",          // Social Security Number
    "EMAIL",        // Email Address
    "PHONE",        // Phone Number
    "ADDRESS",      // Physical Address
    "DOB",          // Date of Birth
    "DOD",          // Date of Death
    "AGE",          // Age
    "DLN",          // Driver's License Number
    "PPN",          // Passport Number
    "NID",          // National ID
    "TAID",         // Tax ID
    "IP",           // IP Address
    "MAC",          // MAC Address
    "IMEI",         // International Mobile Equipment Identity
    "URL",          // Personal Website URL
    "SMH",          // Social Media Handle
    "UN",           // Username
    "PW",           // Password
    "AAD",          // Authentication and Authorization Data
    "DATE",         // Date
    "POB",          // Place of Birth
    "POD",          // Place of Death
    "POE",          // Place of Education
    "LOC",          // Location
    "GEN",          // Gender
    "ETHN",         // Ethnicity
    "REL",          // Religion
    "MS",           // Marital Status
    "SO",           // Sexual Orientation
    "SPL",          // Spoken Language
    "RAC",          // Residency and Citizenship
    "PROF",         // Profession or Job Title
    "EN",           // Employer Name
    "ORG",          // Organization
    "PID",          // Person ID (generic)
    "OPID",         // Operational ID (generic)
    "LPN"           // License Plate Number
]
```

### Finance Domain (18 types)
```json
"entity_type_finance": [
    "PCN",          // Payment Card Number
    "CVV",          // Card Verification Value
    "PCED",         // Payment Card Expiration Date
    "PCI",          // Payment Card Issuer
    "PCLD",         // Payment Card Last Digits
    "BAN",          // Bank Account Number
    "IBAN",         // International Bank Account Number
    "RN",           // Routing Number
    "SWIFT",        // SWIFT/BIC Code
    "BNK",          // Bank Name
    "CN",           // Check Number
    "LMAN",         // Loan or Mortgage Account Number
    "CCA",          // Cryptocurrency Address
    "EIN",          // Employer Identification Number
    "ITIN",         // Individual Taxpayer Identification Number
    "MAMOUNT",      // Monetary Amount
    "FMETRIC",      // Financial Metric
    "RSCORE"        // Risk Score
]
```

### Medical Domain (22 types)
```json
"entity_type_medical": [
    "MRN",          // Medical Record Number
    "HICN",         // Health Insurance Claim Number
    "HIP",          // Health Insurance Policy Number
    "MED",          // Medicare/Medicaid Number
    "NPI",          // National Provider Identifier
    "RX",           // Prescription Number
    "ICD10",        // ICD-10 Diagnosis Code
    "BC",           // Billing Code (CPT, HCPCS)
    "NDC",          // National Drug Code
    "DRUG",         // Drug Name
    "DRUG_DOSE",    // Drug Dosage
    "DIS",          // Disease Name
    "PROC",         // Procedure Name
    "HOSP",         // Hospital Name
    "MEDD",         // Medical Department
    "DNA",          // Short DNA Sequences
    "LAB_TEST_NAME",        // Laboratory Test Name
    "LAB_RESULT_VALUE",     // Lab Result Value
    "MEASUREMENT_NAME",     // Measurement Name (vitals)
    "MEASUREMENT_VALUE",    // Measurement Value
    "SYMPTOM",              // Clinical Symptom
    "TEST_RESULT_INTERPRETATION"  // Result Interpretation
]
```

---

## 2. Categories (Hierarchical by Domain)

```json
"category": [
    // Person categories
    "person.name",
    "person.govID",
    "person.contact",
    "person.places",
    "person.dates",
    "person.demographics",
    "person.employment",
    "person.organization",
    "person.security",
    "person.device",
    "person.personID",
    "person.operationalID",
    
    // Finance categories
    "finance.pci",
    "finance.banking",
    "finance.crypto",
    "finance.tax",
    "finance.data",
    
    // Medical categories
    "medical.identifiers",
    "medical.codes",
    "medical.terms",
    "medical.data"
]
```

---

## 3. Data Source Types

Based on common data sources in agentic systems:

```json
"data_source_type": [
    // Databases
    "database",
    "sql_database",
    "nosql_database",
    "graph_database",
    "vector_database",
    "data_warehouse",
    
    // File Systems
    "local_file_system",
    "network_file_system",
    "cloud_storage",
    "object_storage",
    
    // APIs & Services
    "rest_api",
    "graphql_api",
    "soap_api",
    "grpc_service",
    "message_queue",
    "event_stream",
    
    // Applications
    "crm_system",
    "erp_system",
    "hr_system",
    "email_system",
    "collaboration_tool",
    
    // Data Sources
    "data_lake",
    "data_stream",
    "log_file",
    "configuration_file",
    "cache",
    "session_store",
    
    // External
    "third_party_api",
    "partner_system",
    "public_dataset",
    
    // Special
    "user_input",
    "agent_memory",
    "model_output",
    "unknown"
]
```

---

## 3a. Data Source Categories (Coarse-Grained)

A coarser classification of `data_source_type`, used when fine-grained source type is unavailable, unnecessary, or when writing simpler rules that only need to distinguish where data is physically/organizationally coming from.

```json
"data_source_category": [
    "local",       // On the same host/process/session as the agent
    "internal",    // Within the organization's trust boundary (internal systems, internal network)
    "external",    // Outside the organization but a known/named party (partners, vendors, third parties)
    "public",      // Open/public internet, no specific counterparty
    "unknown"      // Category cannot be determined
]
```

### Default mapping: `data_source_type` → `data_source_category`

| data_source_type | data_source_category |
|---|---|
| local_file_system | local |
| cache | local |
| session_store | local |
| agent_memory | local |
| user_input | local |
| model_output | local |
| configuration_file | local |
| log_file | local |
| database | internal |
| sql_database | internal |
| nosql_database | internal |
| graph_database | internal |
| vector_database | internal |
| data_warehouse | internal |
| network_file_system | internal |
| cloud_storage | internal |
| object_storage | internal |
| rest_api | internal |
| graphql_api | internal |
| soap_api | internal |
| grpc_service | internal |
| message_queue | internal |
| event_stream | internal |
| crm_system | internal |
| erp_system | internal |
| hr_system | internal |
| email_system | internal |
| collaboration_tool | internal |
| data_lake | internal |
| data_stream | internal |
| third_party_api | external |
| partner_system | external |
| public_dataset | public |
| unknown | unknown |

**Notes:**
- `cloud_storage`/`object_storage`/`network_file_system` default to `internal` on the assumption they are organization-owned tenancy; override to `external` if the specific instance is vendor/partner-owned.
- `rest_api`/`graphql_api`/`soap_api`/`grpc_service`/`message_queue`/`event_stream` default to `internal`; override to `external` when the specific endpoint is operated by a third party (this is what `third_party_api` is for when the type is already known at classification time).

---

## 4. Data Destination Types

Based on use cases in final_examples.md:

```json
"data_destination_type": [
    // Internal Systems
    "internal_database",
    "internal_api",
    "internal_storage",
    "internal_cache",
    "internal_log",
    "internal_analytics",
    
    // External LLMs (from use cases)
    "external_llm",
    "openai_api",
    "anthropic_api",
    "google_ai_api",
    "azure_openai",
    "bedrock_api",
    
    // External Services
    "external_api",
    "external_storage",
    "external_analytics",
    "third_party_service",
    "partner_system",
    
    // Communication
    "email",
    "slack",
    "teams",
    "webhook",
    "notification_service",
    
    // Public/External
    "public_endpoint",
    "public_storage",
    "search_engine",
    "social_media",
    
    // Development
    "github",
    "gitlab",
    "code_repository",
    "ci_cd_pipeline",
    
    // Monitoring & Logging
    "monitoring_system",
    "logging_service",
    "audit_log",
    "siem_system",
    
    // Special
    "user_display",
    "agent_output",
    "model_training",
    "unknown"
]
```

---

## 4a. Data Destination Categories (Coarse-Grained)

A coarser classification of `data_destination_type`, mirroring [3a. Data Source Categories](#3a-data-source-categories-coarse-grained) for destinations.

```json
"data_destination_category": [
    "local",       // Stays on the same host/process/session as the agent (no egress)
    "internal",    // Within the organization's trust boundary
    "external",    // Outside the organization but a known/named party
    "public",      // Open/public internet, no specific counterparty
    "unknown"      // Category cannot be determined
]
```

### Default mapping: `data_destination_type` → `data_destination_category`

| data_destination_type | data_destination_category |
|---|---|
| user_display | local |
| agent_output | local |
| internal_cache | local |
| internal_log | local |
| internal_database | internal |
| internal_api | internal |
| internal_storage | internal |
| internal_analytics | internal |
| monitoring_system | internal |
| logging_service | internal |
| audit_log | internal |
| siem_system | internal |
| github | internal |
| gitlab | internal |
| code_repository | internal |
| ci_cd_pipeline | internal |
| model_training | internal |
| external_llm | external |
| openai_api | external |
| anthropic_api | external |
| google_ai_api | external |
| azure_openai | external |
| bedrock_api | external |
| external_api | external |
| external_storage | external |
| external_analytics | external |
| third_party_service | external |
| partner_system | external |
| email | external |
| slack | external |
| teams | external |
| webhook | external |
| notification_service | external |
| public_endpoint | public |
| public_storage | public |
| search_engine | public |
| social_media | public |
| unknown | unknown |

**Notes:**
- `azure_openai`/`bedrock_api` are cloud-hosted LLM endpoints operated by a third party (Microsoft/AWS reselling the model), so they default to `external` even when deployed inside the organization's own cloud tenant/VNet. Override to `internal` if your deployment treats the tenant boundary as the trust boundary.
- `github`/`gitlab`/`code_repository`/`ci_cd_pipeline` default to `internal` assuming organization-owned/private instances; override to `external` for public repos or third-party-hosted CI.
- `webhook` defaults to `external` since webhook targets are typically outside the organization; override to `internal` for webhooks calling back into internal services.

---

## 5. Trust Levels (Expanded)

```json
"trust_level": [
    "TRUSTED_INTERNAL",         // Internal systems, full trust
    "TRUSTED_INTERNAL_RESTRICTED", // Internal but restricted access
    "TRUSTED_PARTNER",          // Approved partners with BAA/DPA
    "TRUSTED_VENDOR",           // Approved vendors with contracts
    "UNTRUSTED_EXTERNAL",       // External, no trust
    "UNTRUSTED_PUBLIC",         // Public internet, no trust
    "UNKNOWN",                  // Trust level not determined
    "CONDITIONAL_TRUST"         // Trust depends on context
]
```

---

## 6. Event Types (Expanded)

Based on use cases and common agentic operations:

```json
"event_type": [
    // Data Movement
    "external_sharing",         // Data sent to external destination
    "internal_sharing",         // Data shared internally
    "data_export",              // Data exported from system
    "data_import",              // Data imported into system
    "data_transfer",            // Data transferred between systems
    
    // Data Operations
    "data_read",                // Data read operation
    "data_write",               // Data write operation
    "data_update",              // Data update operation
    "data_delete",              // Data delete operation
    "data_copy",                // Data copy operation
    
    // Processing
    "data_transformation",      // Data transformed/processed
    "data_aggregation",         // Data aggregated
    "data_anonymization",       // Data anonymized
    "data_masking",             // Data masked
    "data_encryption",          // Data encrypted
    
    // Agent Operations
    "agent_query",              // Agent queried data
    "agent_generation",         // Agent generated content
    "agent_tool_use",           // Agent used tool
    "agent_memory_access",      // Agent accessed memory
    "agent_chain_execution",    // Multi-agent chain
    
    // Model Operations
    "model_inference",          // Model inference performed
    "model_training",           // Model training with data
    "model_fine_tuning",        // Model fine-tuning
    
    // Access
    "user_access",              // User accessed data
    "api_access",               // API accessed data
    "unauthorized_access",      // Unauthorized access attempt
    
    // Special
    "policy_violation",         // Policy violation detected
    "anomaly_detected",         // Anomaly detected
    "unknown_event"             // Unknown event type
]
```

---

## 7. Action Types (Expanded)

```json
"action_type": [
    // Basic CRUD
    "read",
    "write",
    "update",
    "delete",
    "create",
    
    // Data Movement
    "send",
    "receive",
    "transfer",
    "copy",
    "move",
    "export",
    "import",
    
    // Execution
    "execute",
    "invoke",
    "call",
    "query",
    
    // Transformation
    "transform",
    "aggregate",
    "filter",
    "join",
    "merge",
    
    // Security
    "encrypt",
    "decrypt",
    "mask",
    "redact",
    "anonymize",
    
    // Access Control
    "grant_access",
    "revoke_access",
    "authenticate",
    "authorize",
    
    // Monitoring
    "log",
    "audit",
    "monitor",
    "alert"
]
```

---

## 8. Location Types (Expanded)

```json
"location_type": [
    // File Systems
    "local_file",
    "network_file",
    "cloud_file",
    "temp_file",
    
    // Databases
    "database",
    "database_table",
    "database_view",
    "database_schema",
    
    // Storage
    "object_storage",
    "block_storage",
    "file_storage",
    "blob_storage",
    
    // Cloud Services
    "s3_bucket",
    "azure_blob",
    "gcs_bucket",
    "box",
    "dropbox",
    "onedrive",
    
    // Memory
    "memory",
    "cache",
    "buffer",
    "session_store",
    
    // Messaging
    "message_queue",
    "event_stream",
    "pub_sub",
    
    // APIs
    "api_endpoint",
    "webhook",
    "rest_endpoint",
    "graphql_endpoint",
    
    // Logs
    "log_file",
    "audit_log",
    "system_log",
    
    // Special
    "agent_memory",
    "model_context",
    "user_session",
    "unknown"
]
```

---

## 9. Processing Entity Types (Expanded)

```json
"processing_entity_type": [
    // Agents
    "agent",
    "ai_agent",
    "autonomous_agent",
    "multi_agent_system",
    
    // Tools
    "tool",
    "plugin",
    "extension",
    "integration",
    
    // Code
    "code",
    "script",
    "function",
    "lambda",
    "microservice",
    
    // Models
    "llm",
    "ml_model",
    "embedding_model",
    "classifier",
    
    // Systems
    "system",
    "service",
    "application",
    "workflow",
    "pipeline",
    
    // Human
    "human",
    "user",
    "operator",
    "reviewer",
    
    // Infrastructure
    "container",
    "vm",
    "serverless_function",
    
    // Special
    "unknown"
]
```

---

## 10. Transformation Types (Expanded)

```json
"transformation_type": [
    // State
    "RAW",                  // Original, unmodified data
    "PROCESSED",            // Processed but not secured
    
    // Security Transformations
    "MASKED",               // Partially masked (e.g., XXX-XX-1234)
    "REDACTED",             // Fully redacted (e.g., [REDACTED])
    "ENCRYPTED",            // Encrypted
    "HASHED",               // Hashed (one-way)
    "TOKENIZED",            // Tokenized (reversible with key)
    
    // Privacy Transformations
    "ANONYMIZED",           // Anonymized (irreversible)
    "PSEUDONYMIZED",        // Pseudonymized (reversible with key)
    "AGGREGATED",           // Aggregated (no individual data)
    "GENERALIZED",          // Generalized (reduced precision)
    "SUPPRESSED",           // Suppressed (removed)
    
    // Data Quality
    "VALIDATED",            // Validated against schema
    "CLEANED",              // Cleaned (errors removed)
    "NORMALIZED",           // Normalized format
    "ENRICHED",             // Enriched with additional data
    
    // Format
    "FORMATTED",            // Format changed
    "COMPRESSED",           // Compressed
    "ENCODED",              // Encoded (e.g., base64)
    
    // Special
    "SYNTHETIC",            // Synthetic/generated data
    "UNKNOWN"               // Unknown transformation
]
```

---

## 11. Rule Categories (Based on Use Cases)

```json
"rule_categories": [
    // PII/Privacy
    "pii_exfiltration",
    "pii_exposure",
    "pii_leakage",
    "privacy_violation",
    "pii_protection",           // Rule exists to protect PII (preventive framing)
    
    // PHI/Healthcare
    "phi_exposure",
    "hipaa_violation",
    "medical_data_leakage",
    "phi_protection",           // Rule exists to protect PHI (preventive framing)
    "hipaa",                    // HIPAA-derived rule, not necessarily a violation
    
    // PCI/Financial
    "pci_violation",
    "financial_data_leakage",
    "payment_data_exposure",
    
    // Data Quality
    "hallucination",
    "data_corruption",
    "data_integrity_violation",
    "cascading_error",
    
    // Access Control
    "unauthorized_access",
    "privilege_escalation",
    "access_violation",
    "access_control",           // Access-control concern, not yet a confirmed violation
    
    // Data Exfiltration
    "data_exfiltration",
    "data_leakage",
    "sensitive_data_disclosure",
    
    // Anomalies
    "anomaly_detected",
    "behavioral_anomaly",
    "volume_anomaly",
    "destination_anomaly",
    
    // Cross-Agent
    "cross_agent_contamination",
    "session_contamination",
    "memory_leak",
    
    // Compliance
    "gdpr_violation",
    "ccpa_violation",
    "sox_violation",
    "regulatory_violation",
    
    // Compound Risks
    "compound_risk",
    "multi_factor_risk",
    
    // Unknown/Fallback
    "unknown_behavior",
    "unknown_destination",
    "unknown_source",
    "fallback"
]
```

---

## 12. Regulatory Tags (Complete)

```json
"regulatory_tags": [
    "PII",              // Personally Identifiable Information (GDPR/CCPA)
    "PHI",              // Protected Health Information (HIPAA)
    "PCI",              // Payment Card Industry data (PCI-DSS)
    "PI",               // Personal Information (general)
    "CREDENTIALS",      // Authentication credentials
    "CONFIDENTIAL",     // Business confidential
    "TRADE_SECRET",     // Trade secrets
    "FINANCIAL",        // Financial data (SOX)
    "BIOMETRIC",        // Biometric data
    "GENETIC",          // Genetic data
    "LOCATION",         // Location data
    "BEHAVIORAL",       // Behavioral data
    "CHILDREN",         // Children's data (COPPA)
    "SENSITIVE"         // Sensitive personal data (GDPR Article 9)
]
```

---

## 13. Enforcement Types (Complete)

```json
"enforcement_type": [
    "allow",                // Allow the action
    "block",                // Block the action
    "redact",               // Redact sensitive fields
    "mask",                 // Mask sensitive fields
    "encrypt",              // Encrypt before allowing
    "anonymize",            // Anonymize before allowing
    "escalate",             // Escalate to human review
    "require_approval",     // Require approval before allowing
    "log_only",             // Log but allow (monitoring mode)
    "warn",                 // Warn but allow
    "throttle",             // Rate limit the action
    "quarantine",           // Quarantine for review
    "audit",                // Audit trail required
    "notify"                // Notify security team
]
```

---

## 14. Risk Levels (Complete)

```json
"risk_level": [
    "critical",     // Immediate action required, severe impact
    "high",         // Urgent attention needed, significant impact
    "medium",       // Should be addressed, moderate impact
    "low",          // Monitor, minimal impact
    "none",         // No risk detected
    "unknown"       // Risk level cannot be determined
]
```

---

## 15. Data States (Expanded)

```json
"data_state": [
    "raw",              // Original, unprocessed
    "processed",        // Processed/transformed
    "masked",           // Masked/redacted
    "encrypted",        // Encrypted
    "in_transit",       // Being transferred
    "at_rest",          // Stored
    "in_use",           // Being processed
    "archived",         // Archived
    "deleted",          // Deleted/purged
    "temporary",        // Temporary/ephemeral
    "cached"            // Cached
]
```

---

## 16. Policy Status

```json
"policy_status": [
    "draft",            // Being developed
    "review",           // Under review
    "approved",         // Approved but not active
    "active",           // Currently enforced
    "inactive",         // Not enforced
    "deprecated",       // Deprecated, will be removed
    "archived"          // Archived for historical reference
]
```

---

## 17. Policy Types

```json
"policy_type": [
    "data_protection",      // Data protection policies
    "access_control",       // Access control policies
    "data_quality",         // Data quality policies
    "compliance",           // Regulatory compliance
    "security",             // Security policies
    "privacy",              // Privacy policies
    "operational",          // Operational policies
    "governance",           // Data governance
    "data_governance",      // Data governance (alias used by the DG-* rule bundle)
    "risk_management",      // Risk management
    "incident_response"     // Incident response
]
```

---

## 18. Confidence Levels (Numeric)

```json
"confidence_level": {
    "description": "0.0 to 1.0 scale",
    "thresholds": {
        "high": "0.9 - 1.0",
        "medium": "0.7 - 0.89",
        "low": "0.5 - 0.69",
        "very_low": "0.0 - 0.49"
    }
}
```

---

## 19. Anomaly Types

```json
"anomaly_type": [
    "destination_novelty",      // New/unusual destination
    "volume_anomaly",           // Unusual data volume
    "frequency_anomaly",        // Unusual frequency
    "field_count_anomaly",      // Unusual number of fields
    "sensitivity_anomaly",      // Unusual sensitivity level
    "time_anomaly",             // Unusual time of access
    "user_behavior_anomaly",    // Unusual user behavior
    "agent_behavior_anomaly",   // Unusual agent behavior
    "data_pattern_anomaly",     // Unusual data pattern
    "access_pattern_anomaly"    // Unusual access pattern
]
```

---

## 20. Temporal Pattern Types

```json
"temporal_pattern_type": [
    "session_contamination",    // Data leaked between sessions
    "cascading_hallucination",  // Errors propagating through chain
    "repeated_violations",      // Multiple violations by same entity
    "escalating_risk",          // Risk increasing over time
    "burst_activity",           // Sudden spike in activity
    "gradual_exfiltration",     // Slow data exfiltration
    "time_based_attack",        // Attack at specific times
    "pattern_deviation"         // Deviation from normal pattern
]
```

---

## Complete Enum Schema

Here's how to organize all enums in the policy schema:

```json
{
    "enums": {
        "entity_types": {
            "person": [/* 38 person entity types */],
            "finance": [/* 18 finance entity types */],
            "medical": [/* 22 medical entity types */]
        },
        "categories": [/* hierarchical categories */],
        "data_source_types": [/* 40+ source types */],
        "data_source_categories": [/* 5 coarse categories: local, internal, external, public, unknown */],
        "data_destination_types": [/* 40+ destination types */],
        "data_destination_categories": [/* 5 coarse categories: local, internal, external, public, unknown */],
        "trust_levels": [/* 8 trust levels */],
        "event_types": [/* 30+ event types */],
        "action_types": [/* 25+ action types */],
        "location_types": [/* 30+ location types */],
        "processing_entity_types": [/* 20+ entity types */],
        "transformation_types": [/* 20+ transformation types */],
        "rule_categories": [/* 30+ rule categories */],
        "regulatory_tags": [/* 14 regulatory tags */],
        "enforcement_types": [/* 14 enforcement types */],
        "risk_levels": [/* 6 risk levels */],
        "data_states": [/* 11 data states */],
        "policy_status": [/* 7 status values */],
        "policy_types": [/* 10 policy types */],
        "anomaly_types": [/* 10 anomaly types */],
        "temporal_pattern_types": [/* 8 pattern types */]
    }
}
```

---

## Usage Recommendations

1. **Start with Core Enums:** Implement entity_types, trust_levels, enforcement_types, risk_levels first
2. **Expand Gradually:** Add more specific enums as use cases emerge
3. **Allow "unknown":** Always include "unknown" option for extensibility
4. **Document Mappings:** Document how external systems map to these enums
5. **Version Control:** Version enum changes (adding values is backward compatible, removing is not)
6. **Validation:** Validate all enum values at policy creation time
7. **Extensibility:** Consider allowing custom values with a prefix (e.g., "custom:my_type")
