"""P-interactions graph algorithm — see README.md and ADR-0025.

The batch graph algorithm that derives entities + interactions from a trace's
spans. Its pure core is :func:`extractor.extract`; production derivation into the
real ``entities`` / ``interactions`` tables is done by
:mod:`data_governance.processors.interactions.graph_driver` (via ``graph_adapter``
+ ``state.flush``). :mod:`cli` is a dev/debug tool that materialises the
intermediate graph tables only.
"""

from .extractor import extract

__all__ = ["extract"]
