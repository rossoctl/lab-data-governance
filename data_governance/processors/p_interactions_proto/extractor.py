"""Compatibility shim. The extractor logic moved to procedure.py in round 3.

THROWAWAY prototype. See NOTES.md for the round-1, round-2, and round-3
history of how this module evolved.
"""

from __future__ import annotations

from .procedure import (  # noqa: F401  (re-export)
    ExtractResult,
    ProtoEntity,
    ProtoEntitySpan,
    ProtoInteraction,
    ProtoInteractionSpan,
    ProtoPayload,
    extract,
)
