"""P-interactions processor.

A Layer-2 processor (sibling of ``otlp_receiver``) that consumes the ``spans``
table by ``seq`` and materialises the derived interaction graph — ``entities``,
``interactions``, ``entity_spans``, ``interaction_spans``, ``interaction_payloads``
— into Postgres.

The classification logic was ported verbatim from the verified P-interactions
prototype (``procedure.py`` + ``caller_inference.py``); that prototype tree has
since been removed now that the productized processor is the source of truth.
Only the *state layer* differs: where the prototype accumulated all state in
memory, this processor rehydrates the lineage region of derived rows from the
database before each span and flushes the re-derived region back afterwards, so
the per-span procedure runs against a DB-backed store (``state.py``).

Run as ``python -m data_governance.processors.interactions``.
"""
