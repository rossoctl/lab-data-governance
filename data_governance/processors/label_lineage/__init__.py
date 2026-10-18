"""P-label-lineage — the stream consumer that derives label lineage per trace
(the pure derivation is :mod:`data_governance.label_lineage`): :mod:`.driver`
drains ready legs and re-derives their traces, :mod:`.store` writes the
derivation to migration 0024's tables.

Run as ``python -m data_governance.processors.label_lineage``.
"""
