"""The leg-ready observer that wires the interaction risk engine into the
``dg_interaction_leg_ready`` consumer (issue #158).

This is the missing connective tissue #101 deliberately deferred: a
:data:`~data_governance.processors.leg_ready.driver.Observer` that drives
``prepare_interaction_risk`` for every ready leg's interaction, so FR-DAS-012
conditions 1 (new leg) and 2 (new classification) produce risk recomputes
automatically — the leg-ready channel fires for exactly those two events.

Two-phase, per the seam's contract: the observer call runs the engine's
compute half — evidence gathering, fingerprint comparison, and (only when the
evidence changed) the OPA HTTP round-trip, with no transaction held — and
returns the engine's write half, which the drain runs inside the leg's
delivery transaction, atomically with the cursor advance. A failed write
therefore rolls the cursor back and the leg is re-delivered: the silent-skip
hole the issue exists to close cannot occur.

Failure semantics (issue #158 scope item 3 — the decisions, and why):

- **Transient OPA failure** (:class:`OpaTimeoutError`, :class:`OpaRequestError`
  — OPA down, unreachable, or 5xx-ing): raise
  :class:`~data_governance.processors.leg_ready.driver.LegDeferred`. The
  stream holds at this leg and retries every wake (poll backstop). An OPA
  outage thus delays risk computation but loses nothing, and the first
  successful wake catches the stream up. Head-of-line blocking is the point:
  order and completeness beat progress here.
- **Unusable OPA answer** (:class:`OpaResponseError` — OPA responded 2xx but
  the body has no usable decision): also a hold, NOT a skip. It means the
  loaded policy is broken (the shipped policy's fallback rule makes a missing
  decision impossible — see rules/rego.py's combining fallback), which an
  operator can fix and reload; skipping would permanently lose legs to a
  config mistake. The
  WARNING log every retry wake is the operator signal.
- **Poison leg** (:class:`InteractionNotFoundError` — the leg references an
  interaction that does not exist; cannot ever compute): log ERROR and
  deliver nothing, letting the cursor advance. A permanently-unprocessable
  leg must not wedge the stream forever (scope item 3's other half). This is
  the ONLY skip path, and it is loudly logged.
- Anything else (DB faults, bugs) propagates and crashes the processor —
  k8s restarts it and the durable cursor re-delivers; absorbing unknown
  errors would trade a visible crash for silent data loss.

Backpressure (scope item 4): one *potential* OPA round-trip per ready leg per
drain (batch ≤ ``_DRAIN_BATCH``), bounded by ``RISK_OPA_TIMEOUT_SECONDS`` ×
retries each. The evidence fingerprint keeps this sub-linear in steady state:
a re-delivered or unchanged-evidence leg reuses the cached decision with no
HTTP call at all, so bursts of legs for already-evaluated interactions drain
at DB speed. If sustained OPA latency ever dominates, the batch size — not
this seam — is the tuning knob.
"""

from __future__ import annotations

import logging

from data_governance.processors.leg_ready.driver import Leg, LegDeferred, Observer
from data_governance.risk.engine.compute import RecordWrite, prepare_interaction_risk
from data_governance.risk.engine.evidence import InteractionNotFoundError
from data_governance.risk.engine.opa import OpaClient, OpaError

__all__ = ["make_risk_observer"]

log = logging.getLogger(__name__)


def make_risk_observer(opa_client: OpaClient) -> Observer:
    """Build the leg-ready observer that computes interaction risk for each
    delivered leg's interaction via *opa_client* (see module docstring for
    the failure-semantics contract)."""

    def observe(leg: Leg) -> RecordWrite | None:
        try:
            return prepare_interaction_risk(
                leg.interaction_id, opa_client=opa_client
            )
        except InteractionNotFoundError:
            log.error(
                "leg seq=%d references missing interaction_id=%s — "
                "permanently unprocessable, skipping (cursor will advance)",
                leg.seq, leg.interaction_id,
            )
            return None
        except OpaError as exc:
            raise LegDeferred(
                f"risk compute for interaction_id={leg.interaction_id} "
                f"deferred: {exc}"
            ) from exc

    return observe
