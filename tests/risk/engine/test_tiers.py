"""The verdict in tiers of evidence, at the level of the decision record.

- ``utils.strictest``: the strictest decision of one snapshot asked tier by tier, and the tier that decided it;
  a later allow never replaces a block.
- ``labels.tiers``: what each tier hands the policy; tier 1 leaves out every edge that rests on a crossing, a
  crossing inside one conversation (R7) included.
- The engine asks a lower tier only when its input differs, caches the full evidence's decision only, writes one
  ``interaction_risk_tiers`` row per version (the tier, and ``late`` when the version is stricter than the latest
  earlier one that evidenced the same legs), and counts the late finding. The record is what it was before
  tiers: with inherited labels off, and on with a policy that never relaxes on more evidence.
"""

from __future__ import annotations

import dataclasses
import itertools

import psycopg
import pytest

from data_governance.processors.classification.verdict import Verdict
from data_governance.processors.leg_ready import metrics
from data_governance.risk.engine import compute, labels, utils
from data_governance.risk.engine.evidence import Evidence
from data_governance.risk.engine.opa import OpaDecision, OpaTimeoutError


def _d(enforcement: str | None, rule: str = "R") -> utils.PolicyDecision:
    return utils.PolicyDecision("high", enforcement, [], None, [rule], 0.5, "1")


# --- the strictest decision so far ------------------------------------------------------------------


@pytest.mark.parametrize("tiers, decision, tier", [
    (["allow"], "allow", 0),
    (["block", "allow", "allow"], "block", 0),
    (["allow", "block", "allow"], "block", 1),
    (["allow", "allow", "block"], "block", 2),
    (["block", "block", "block"], "block", 0),
    (["redact", "allow"], "redact", 1),           # only a block is decided early
    (["allow", "warn", "redact"], "redact", 2),
    ([None, "allow"], "allow", 1),                # an unranked value ranks last, as the catalog ranks it
    (["unknown-to-the-catalog", "allow"], "allow", 1),
])
def test_the_strictest_decision_and_the_tier_that_decided_it(tiers, decision, tier) -> None:
    got, at = utils.strictest([_d(e) for e in tiers])
    assert (got.enforcement_type, at) == (decision, tier)


def test_a_later_allow_never_replaces_a_block() -> None:
    for n in (1, 2, 3):
        for blocked in range(n):
            tiers = ["allow"] * n
            tiers[blocked] = "block"
            got, at = utils.strictest([_d(e) for e in tiers])
            assert (got.enforcement_type, at) == ("block", blocked), tiers


def test_among_equally_strict_decisions_the_highest_tier_s_is_carried() -> None:
    """So the record keeps the full evidence's rules when no lower tier was stricter."""
    got, at = utils.strictest([_d("block", "own"), _d("block", "own+inherited")])
    assert got.triggered_rules == ["own+inherited"] and at == 0


def test_stricter_follows_the_catalog_order() -> None:
    assert utils.stricter("block", "allow") and utils.stricter("allow", None)
    assert not utils.stricter("allow", "block") and not utils.stricter("block", "block")


# --- what each tier hands the policy ----------------------------------------------------------------


def _edge(start: int, *, level: str | None = None, origin: str | None = None) -> labels.InheritedEdge:
    return labels.InheritedEdge(trace_id="T", entity_type="SSN", regulatory_tags=("PII",),
                                sensitivity_level="RESTRICTED", match_kind="whole", matched_text="123-45-6789",
                                matched_start=start, matched_end=start + 11, origin_trace_id=origin,
                                crossed_level=level)


INSIDE = _edge(10)
CROSSED = _edge(40, level="value_only", origin="earlier")
R7 = _edge(70, level="corroborated", origin="T")           # a crossing between two calls of this conversation


def test_off_is_one_tier_and_on_alone_two() -> None:
    on = labels.Inherited(mode="on", by_leg={"request": [INSIDE, R7]})
    assert labels.tiers(None) == [None]
    assert labels.tiers(on) == [None, on]


def test_with_the_cross_switch_tier_1_leaves_out_every_crossed_edge() -> None:
    full = labels.Inherited(mode="on", cross=True, by_leg={"request": [INSIDE, CROSSED, R7], "response": [CROSSED]})
    zero, one, two = labels.tiers(full)
    assert zero is None and two is full
    assert one.by_leg == {"request": [INSIDE], "response": []} and one.cross


# --- the engine --------------------------------------------------------------------------------------

CLEAN = Verdict(sensitivity_level="PUBLIC", regulatory_tags=[], contains_identity_bundle=False,
                is_personalized=False, primary_domain=None, findings=[], model_version=1)
LABELLED = dataclasses.replace(CLEAN, sensitivity_level="RESTRICTED", regulatory_tags=["PII"])
_IX = "ix-tiers"


class _Policy:
    """Blocks when any item of the request flow carries PII, naming how many items it read: a policy that never
    relaxes on more evidence."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def evaluate(self, *, interaction_id: str, opa_input: dict) -> OpaDecision:
        self.calls.append(opa_input)
        items = opa_input["flows"]["request"]["data_items"]
        pii = any("PII" in item["regulatory_tags"] for item in items)
        return OpaDecision(risk_level="high" if pii else "low", enforcement_type="block" if pii else "allow",
                           allowed_actions=[], explanation=None, triggered_rules=[f"items-{len(items)}"],
                           confidence=0.5, policy_version="1")


def _evidence(classification: Verdict = CLEAN, inherited: labels.Inherited | None = None) -> Evidence:
    return Evidence(interaction_id=_IX, trace_id="T", caller_entity_id="a", callee_entity_id="b",
                    legs=[utils.LegEvidence("request", "h")], span_ids=[], classifications={"request": classification},
                    inherited=inherited)


@pytest.fixture()
def interaction(configured_db: str) -> str:
    with psycopg.connect(configured_db) as conn:
        conn.execute("INSERT INTO interactions (id, trace_id, caller_entity_id, callee_entity_id, summary) "
                     "VALUES (%s, 'T', 'a', 'b', 'x')", (_IX,))
        conn.commit()
    return configured_db


def _compute(monkeypatch, evidence: Evidence, policy: _Policy) -> None:
    monkeypatch.setattr(compute, "gather_evidence", lambda _iid: evidence)
    compute.compute_interaction_risk(_IX, opa_client=policy)


def _rows(dsn: str) -> list[tuple]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT r.version, r.enforcement_type, r.triggered_rule_ids, t.tier, t.late "
            "FROM interaction_risk_records r LEFT JOIN interaction_risk_tiers t USING (interaction_id, version) "
            "WHERE r.interaction_id = %s ORDER BY r.version", (_IX,)).fetchall()


@pytest.mark.parametrize("inherited, calls", [
    (None, 1),                                                                          # inherited labels off
    (labels.Inherited(mode="on", by_leg={}), 1),                                        # nothing inherited
    (labels.Inherited(mode="on", by_leg={"request": [INSIDE]}), 2),
    (labels.Inherited(mode="on", cross=True, by_leg={"request": [INSIDE]}), 2),         # tier 2 = tier 1
    (labels.Inherited(mode="on", cross=True, by_leg={"request": [CROSSED]}), 2),        # tier 1 = tier 0
    (labels.Inherited(mode="on", cross=True, by_leg={"request": [INSIDE, CROSSED]}), 3),
])
def test_a_tier_is_asked_only_when_its_input_differs(interaction: str, monkeypatch, inherited, calls) -> None:
    policy = _Policy()
    _compute(monkeypatch, _evidence(inherited=inherited), policy)
    assert len(policy.calls) == calls
    assert len({repr(c) for c in policy.calls}) == calls
    with psycopg.connect(interaction) as conn:     # only the full evidence's decision is cached
        assert conn.execute("SELECT count(*) FROM interaction_policy_decisions").fetchone() == (1,)


def test_tier_1_hands_the_policy_no_crossed_label(interaction: str, monkeypatch) -> None:
    policy = _Policy()
    _compute(monkeypatch, _evidence(inherited=labels.Inherited(mode="on", cross=True,
                                                               by_leg={"request": [INSIDE, CROSSED, R7]})), policy)
    full, zero, one = policy.calls       # the full evidence first (the cached decision), then tiers 0 and 1
    assert [len(c["flows"]["request"]["data_items"]) for c in (zero, one, full)] == [1, 2, 3]
    assert "crossed" not in one["flows"]["request"] and "crossed" in full["flows"]["request"]
    assert [e["start"] for e in one["flows"]["request"]["data_items"][1]["entities"]] == [10]


def test_with_inherited_labels_off_the_record_is_the_policy_s_answer_at_tier_0(interaction: str, monkeypatch) -> None:
    _compute(monkeypatch, _evidence(LABELLED), _Policy())
    assert _rows(interaction) == [(1, "block", ["items-1"], 0, False)]


def test_the_classifier_alone_blocks_at_tier_0_and_the_record_keeps_the_full_evidence_s_rules(
        interaction: str, monkeypatch) -> None:
    """K29's mechanism: a block at tier 0 is decided there, and the record is the one the engine wrote before
    tiers (the full evidence's decision), because the policy never relaxes."""
    inherited = labels.Inherited(mode="on", cross=True, by_leg={"request": [INSIDE, CROSSED]})
    _compute(monkeypatch, _evidence(LABELLED, inherited), _Policy())
    assert _rows(interaction) == [(1, "block", ["items-3"], 0, False)]


def test_a_stricter_version_is_a_late_finding_and_a_relaxed_one_is_not(interaction: str, monkeypatch) -> None:
    """K30's and K25's mechanisms in one history: allow at tier 1; the crossed label blocks at tier 2, late; a
    correction takes it away and the decision relaxes (strictest-so-far holds inside one snapshot only); a new
    block is late again; a block after a block is not."""
    metrics.make_registry()
    unlabelled = dataclasses.replace(INSIDE, regulatory_tags=())       # known inside the conversation, no PII
    conv = labels.Inherited(mode="on", cross=True, by_leg={"request": [unlabelled]})
    crossed = dataclasses.replace(conv, by_leg={"request": [*conv.by_leg["request"], CROSSED]})
    for evidence in (_evidence(inherited=conv), _evidence(inherited=crossed), _evidence(inherited=conv),
                     _evidence(LABELLED, crossed), _evidence(LABELLED, conv)):
        _compute(monkeypatch, evidence, _Policy())
    assert [(v, e, t, late) for v, e, _rules, t, late in _rows(interaction)] == [
        (1, "allow", 2, False), (2, "block", 2, True), (3, "allow", 2, False), (4, "block", 0, True),
        (5, "block", 0, False)]
    assert metrics.late_findings_total._value.get() == 2


def test_an_interaction_with_no_request_leg_has_late_findings_on_its_own_legs(interaction: str,
                                                                              monkeypatch) -> None:
    """A response-only exchange: a version stricter than the one before on the same (response) leg is late."""
    for evidence in (_legs(response=CLEAN), _legs(response=LABELLED), _legs(request=CLEAN)):
        _compute(monkeypatch, evidence, _AnyFlow())
    assert [(e, late) for _v, e, _rules, _t, late in _rows(interaction)] == [
        ("allow", False), ("block", True), ("allow", False)]


class _AnyFlow(_Policy):
    """Blocks when any item of any flow carries PII, as the compiled catalog walks every flow."""

    def evaluate(self, *, interaction_id: str, opa_input: dict) -> OpaDecision:
        self.calls.append(opa_input)
        pii = any("PII" in item["regulatory_tags"] for flow in opa_input["flows"].values()
                  for item in flow["data_items"])
        return OpaDecision(risk_level="high" if pii else "low", enforcement_type="block" if pii else "allow",
                           allowed_actions=[], explanation=None, triggered_rules=["any-flow"], confidence=0.5,
                           policy_version="1")


def _legs(**classified: Verdict) -> Evidence:
    return dataclasses.replace(_evidence(), legs=[utils.LegEvidence(leg, f"h-{leg}") for leg in classified],
                               classifications=dict(classified))


@pytest.mark.parametrize("first, then", [
    (_legs(request=CLEAN), _legs(request=CLEAN, response=LABELLED)),      # an ordinary request, then its response
    (_legs(response=CLEAN), _legs(request=LABELLED, response=CLEAN)),     # a response-only allow, then the request
])
def test_a_version_that_adds_a_leg_is_not_a_late_finding(interaction: str, monkeypatch, first, then) -> None:
    """With both switches off: the stricter version decides new evidence, which no earlier decision could have
    answered, so it is not late and the counter stays at 0."""
    metrics.make_registry()
    for evidence in (first, then):
        _compute(monkeypatch, evidence, _AnyFlow())
    assert [(v, e, late) for v, e, _rules, _t, late in _rows(interaction)] == [(1, "allow", False), (2, "block", False)]
    assert metrics.late_findings_total._value.get() == 0


def test_unchanged_evidence_writes_no_version_and_no_tier_row(interaction: str, monkeypatch) -> None:
    for _ in itertools.repeat(None, 2):
        _compute(monkeypatch, _evidence(LABELLED), _Policy())
    assert len(_rows(interaction)) == 1


# --- a lower tier the policy cannot answer -----------------------------------------------------------

BOTH = labels.Inherited(mode="on", cross=True, by_leg={"request": [dataclasses.replace(INSIDE, regulatory_tags=()),
                                                                   CROSSED]})


class _LowerTiersTimeOut(_Policy):
    """Answers the full evidence (asked first when not cached); every lower tier times out."""

    def __init__(self, *, full_answered: bool = True) -> None:
        super().__init__()
        self._full_answered = full_answered

    def evaluate(self, *, interaction_id: str, opa_input: dict) -> OpaDecision:
        if self._full_answered and not self.calls:
            return super().evaluate(interaction_id=interaction_id, opa_input=opa_input)
        self.calls.append(opa_input)
        raise OpaTimeoutError("policy unreachable")


def _cache_the_full_decision(dsn: str, monkeypatch, evidence: Evidence) -> None:
    """A decision cached before: computed once, then its record taken away, so the next computation finds the
    cached full decision and must write a record again."""
    _compute(monkeypatch, evidence, _Policy())
    with psycopg.connect(dsn) as conn:
        conn.execute("DELETE FROM interaction_risk_tiers")
        conn.execute("DELETE FROM interaction_risk_records")
        conn.commit()


def test_a_cached_full_block_stands_when_a_lower_tier_cannot_be_asked(interaction: str, monkeypatch) -> None:
    _cache_the_full_decision(interaction, monkeypatch, _evidence(inherited=BOTH))
    policy = _LowerTiersTimeOut(full_answered=False)
    _compute(monkeypatch, _evidence(inherited=BOTH), policy)
    assert len(policy.calls) == 1                         # the full decision came from the cache
    assert [(e, t, late) for _v, e, _r, t, late in _rows(interaction)] == [("block", 2, False)]


def test_a_fresh_full_block_stands_when_a_lower_tier_cannot_be_asked(interaction: str, monkeypatch) -> None:
    _compute(monkeypatch, _evidence(inherited=BOTH), _LowerTiersTimeOut())
    assert [(e, t) for _v, e, _r, t, _late in _rows(interaction)] == [("block", 2)]


@pytest.mark.parametrize("cached", [False, True])
def test_a_full_allow_is_deferred_when_a_lower_tier_cannot_be_asked(interaction: str, monkeypatch, cached) -> None:
    """The missing tier could still be stricter: the computation raises, as before tiers on a policy failure."""
    conv = labels.Inherited(mode="on", cross=True, by_leg={"request": [dataclasses.replace(INSIDE, regulatory_tags=()),
                                                                       dataclasses.replace(CROSSED, regulatory_tags=())]})
    if cached:
        _cache_the_full_decision(interaction, monkeypatch, _evidence(inherited=conv))
    with pytest.raises(OpaTimeoutError):
        _compute(monkeypatch, _evidence(inherited=conv), _LowerTiersTimeOut(full_answered=not cached))
    assert _rows(interaction) == []



# --- the early block ---------------------------------------------------------------------------------
def _early(monkeypatch, evidence: Evidence, policy: _Policy) -> bool:
    """Ask the early question on *evidence* and run its write, if it has one."""
    from data_governance import db
    monkeypatch.setattr(compute, "gather_evidence", lambda _iid: evidence)
    write = compute.prepare_early_block(_IX, opa_client=policy)
    if write is not None:
        with db.transaction() as tx:
            write(tx)
    return write is not None


def _cached_decisions(dsn: str) -> int:
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT count(*) FROM interaction_policy_decisions WHERE interaction_id = %s",
                            (_IX,)).fetchone()[0]


BOTH = labels.Inherited(mode="on", cross=True, by_leg={"request": [INSIDE, CROSSED]})


def test_an_early_block_is_decided_on_the_classification_alone(interaction: str, monkeypatch) -> None:
    """The classifier's own finding blocks: the record is written from tier 0's input (one item, nothing
    inherited read), with its tier row, and the decision cache is not touched."""
    metrics.make_registry()
    policy = _Policy()
    assert _early(monkeypatch, _evidence(LABELLED, BOTH), policy)
    assert _rows(interaction) == [(1, "block", ["items-1"], 0, False)]
    assert len(policy.calls) == 1 and _cached_decisions(interaction) == 0
    assert metrics.early_blocks_total._value.get() == 1


def test_the_early_question_writes_nothing_unless_it_blocks(interaction: str, monkeypatch) -> None:
    """A crossed label would block at tier 2; the classification alone does not: no record, no cached decision."""
    metrics.make_registry()
    policy = _Policy()
    assert not _early(monkeypatch, _evidence(CLEAN, BOTH), policy)
    assert _rows(interaction) == [] and _cached_decisions(interaction) == 0
    assert [len(c["flows"]["request"]["data_items"]) for c in policy.calls] == [1]
    assert metrics.early_blocks_total._value.get() == 0


def test_an_early_block_never_replaces_a_record_of_the_same_legs(interaction: str, monkeypatch) -> None:
    """The full record landed first: the early block, computed on older readiness, writes nothing."""
    metrics.make_registry()
    _compute(monkeypatch, _evidence(LABELLED, BOTH), _Policy())
    before = _rows(interaction)
    assert before == [(1, "block", ["items-3"], 0, False)]
    assert _early(monkeypatch, _evidence(LABELLED, BOTH), _Policy())      # it would block, and has a write
    assert _rows(interaction) == before and metrics.early_blocks_total._value.get() == 0


def test_the_full_computation_follows_an_early_block(interaction: str, monkeypatch) -> None:
    """After the early block the full evidence is computed as ever: a record the labels change is a new version,
    not a late finding (block after block); the same computation again writes nothing."""
    metrics.make_registry()
    assert _early(monkeypatch, _evidence(LABELLED, BOTH), _Policy())
    for _ in range(2):
        _compute(monkeypatch, _evidence(LABELLED, BOTH), _Policy())
    assert _rows(interaction) == [(1, "block", ["items-1"], 0, False), (2, "block", ["items-3"], 0, False)]
    assert metrics.late_findings_total._value.get() == 0



def test_the_early_record_carries_the_classification_alone(interaction: str, monkeypatch) -> None:
    """Nothing inherited reaches the early record: its summary is the one of a record with no labels to inherit."""
    assert _early(monkeypatch, _evidence(LABELLED, BOTH), _Policy())
    with psycopg.connect(interaction) as conn:
        (early,) = conn.execute("SELECT classification_summary FROM interaction_risk_records WHERE interaction_id = %s",
                                (_IX,)).fetchone()
    assert early == utils.classification_summary({"request": LABELLED}, None)
    assert early != utils.classification_summary({"request": LABELLED}, BOTH), "the full record's does carry them"


def test_the_early_guard_is_per_set_of_legs(interaction: str, monkeypatch) -> None:
    """A record that evidences only the request does not stop an early block on request and response: an early
    block is the first answer for its legs, not for the exchange."""
    _compute(monkeypatch, _legs(request=CLEAN), _Policy())
    assert _early(monkeypatch, _legs(request=LABELLED, response=CLEAN), _Policy())
    assert [(v, e, t) for v, e, _r, t, _late in _rows(interaction)] == [(1, "allow", 0), (2, "block", 0)]
    assert not _early_wrote(monkeypatch, _legs(request=LABELLED, response=CLEAN)), "and only the first for them"


def _early_wrote(monkeypatch, evidence: Evidence) -> bool:
    from data_governance import db
    monkeypatch.setattr(compute, "gather_evidence", lambda _iid: evidence)
    write = compute.prepare_early_block(_IX, opa_client=_Policy())
    with db.transaction() as tx:
        return write(tx)


def test_the_early_write_waits_for_a_full_write_in_flight(interaction: str, monkeypatch) -> None:
    """The schedule the interaction lock is there for: the early write would read "no record" while the full
    record is inserted and not yet committed, and insert its own after. Under the lock it reads only after the
    commit, finds the record and writes nothing."""
    from .interleave import read_between_write_and_commit
    monkeypatch.setattr(compute, "gather_evidence", lambda _iid: _evidence(LABELLED, BOTH))
    full = compute.prepare_interaction_risk(_IX, opa_client=_Policy())
    early = compute.prepare_early_block(_IX, opa_client=_Policy())
    errors = read_between_write_and_commit(full, early, read_marker="AND legs_evidenced = %s LIMIT 1",
                                           monkeypatch=monkeypatch)
    assert errors == []
    assert _rows(interaction) == [(1, "block", ["items-3"], 0, False)]
