"""System tests for label lineage on authored spans.

A case is one line (``tests/system/cases/<id>/case.yaml``), rendered to sidecar
spans, sent through the real OTLP receiver and the real processors against a
real Postgres and a real OPA, then read back as rows. See ``README.md`` here.
"""
