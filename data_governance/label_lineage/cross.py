"""Label lineage across conversations: what a persistent entity received, and the
values it sends again (rules R1-R9).

- :class:`Holding` — one time a persistent entity received a labelled value: the
  occurrence at the leg it received, the record that held the value there, the
  interval in event time it is valid for, and how the leg knew the label.
- :class:`Memory` — the holdings of the entities one derivation reads, indexed by
  entity and value text; :meth:`Memory.found` searches one payload at its start
  boundaries, one lookup per distinct held length.
- :func:`leaving` — the match at a payload a persistent entity sends: by text, the
  common values dropped, the rest asserted by origin under the rule table.
- :func:`records` — where a value's record is in a structured payload (R4).

The limits, each unset by default and each applied at the leaving leg:
holdings per entity (more occurrences valid at the payload's time than the limit: nothing
is searched), payload bytes (only the first bytes of the projected payload are searched; what
is found there is asserted, at a level the whole text could raise; with the candidates
limit also set, the candidates counted are the prefix's, so the prefix can assert what
the whole text, over that limit, would not), candidates per payload (more candidates
than the limit: nothing is asserted). A leg
a limit cut is marked with the limit's name (:data:`LIMITS`); a cut result is never
silently partial. No limit has a default number, because none can be reasoned to
without traffic: the producer already caps a payload (``max_payload_bytes``, 4096 by
default), M bounds the conversations behind each value, and how many values an entity
holds at once is its arrival rate times the retention, which nothing here knows.

The rule table: in one record of the in-leg, no anchoring identifier among the
matched values draws nothing; one anchoring identifier alone is *value-only*; an
anchoring identifier and another matched value of the same record are
*corroborated*. A value without a record (an unstructured payload, part of a longer
string) is value-only at most. Pure: the driver loads the holdings, the fold calls
this module, nothing here reads the database, and the clock only at startup
(:func:`configured` checks that the retention fits the calendar).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import os
from collections import defaultdict
from collections.abc import Iterable, Mapping

from data_governance.processors.classification.config import load_entity_metadata

from .units import LegKey, Occurrence, ProjectedPayload
from .value import is_whole_value

VALUE_ONLY = "value_only"
CORROBORATED = "corroborated"
#: How surely a label at a leg is the value's: by a value-only crossing, by a corroborated
#: one, or for certain (found by the classifier, or carried inside the conversation). Along
#: one path the weakest step holds; of several paths or origins the strongest (R3).
CERTAIN = "certain"
_RANK = {VALUE_ONLY: 0, CORROBORATED: 1, CERTAIN: 2}

# The anchoring identifiers: the classifier's person identifiers (its ``personID``
# data type), less those that do not name one person by a number issued to them: a
# person's name (PN), a chosen username (UN, system accounts recur everywhere), an
# IP address (shared and reassigned) and a check number (a short sequence per
# checkbook). Each one kept is unique to its holder, so an equal value is the same
# holder's (R5); device, vehicle and wallet identifiers (IMEI, MAC, LPN, CCA) are
# kept for that reason. Operational identifiers and plain data never anchor.
DEFAULT_ANCHORS: frozenset[str] = frozenset({
    "SSN", "NID", "PPN", "DLN", "TAID", "ITIN", "PID",
    "MRN", "MED", "HICN", "HIP",
    "BAN", "IBAN", "LMAN", "PCN", "CCA",
    "IMEI", "MAC", "LPN",
})
DEFAULT_COMMON_LIMIT = 3                        # M (R9)
DEFAULT_RETENTION = dt.timedelta(minutes=15)    # time-based entities (R9)

SWITCH_ENV_VAR = "LABEL_CROSS_CONVERSATION"
COMMON_LIMIT_ENV_VAR = "LABEL_CROSS_COMMON_LIMIT"
RETENTION_ENV_VAR = "LABEL_CROSS_RETENTION_SECONDS"
ANCHORS_ENV_VAR = "LABEL_CROSS_ANCHORS"
_TAG_TABLE = "data_governance/processors/classification/_config_data/EntityTypesForTokenClassification_with_tags.csv"
HOLDINGS_LIMIT_ENV_VAR = "LABEL_CROSS_HOLDINGS_PER_ENTITY"
PAYLOAD_BYTES_ENV_VAR = "LABEL_CROSS_PAYLOAD_BYTES"
CANDIDATES_LIMIT_ENV_VAR = "LABEL_CROSS_CANDIDATES_PER_PAYLOAD"

#: The limits a leaving leg can be cut by, as its mark names them.
HOLDINGS_PER_ENTITY = "holdings_per_entity"
PAYLOAD_BYTES = "payload_bytes"
CANDIDATES_PER_PAYLOAD = "candidates_per_payload"
LIMITS = (HOLDINGS_PER_ENTITY, PAYLOAD_BYTES, CANDIDATES_PER_PAYLOAD)


class InvalidCrossing(ValueError):
    """The cross-conversation configuration is not one the processor can run with."""


@dataclasses.dataclass(frozen=True)
class Crossing:
    """What a cross-conversation derivation runs with: the anchoring label types, M
    (a value held from more than M other conversations is common), the retention, and
    the limits (``None``: no limit)."""

    anchors: frozenset[str] = DEFAULT_ANCHORS
    common_limit: int = DEFAULT_COMMON_LIMIT
    retention: dt.timedelta = DEFAULT_RETENTION
    holdings_limit: int | None = None
    payload_bytes: int | None = None
    candidates_limit: int | None = None


def weakest(*levels: str) -> str:
    return min(levels, key=_RANK.__getitem__)


def strongest(levels: Iterable[str]) -> str:
    return max(levels, key=_RANK.__getitem__)


def switch_on(env: Mapping[str, str] = os.environ) -> bool:
    """Whether ``LABEL_CROSS_CONVERSATION`` says on (unset: off); anything but on/off
    raises. Label lineage and the risk engine read the switch through this one parser."""
    switch = (env.get(SWITCH_ENV_VAR) or "off").strip().lower()
    if switch not in ("on", "off"):
        raise InvalidCrossing(f"{SWITCH_ENV_VAR} must be on or off; got {env.get(SWITCH_ENV_VAR)!r}")
    return switch == "on"


def configured(env: Mapping[str, str] = os.environ) -> Crossing | None:
    """The crossing configuration from the environment, ``None`` while the switch is
    off (unset: off). Every setting is parsed whether the switch is on or off, so an
    invalid one stops the processor at startup either way: anything but on/off, a
    limit that is not a positive integer, a retention the calendar cannot hold, an
    empty anchor list or an anchor that is no label type of the classifier raises;
    nothing falls back. A limit left unset is no limit."""
    on = switch_on(env)
    crossing = Crossing(anchors=_anchors(env), common_limit=_positive(env, COMMON_LIMIT_ENV_VAR, DEFAULT_COMMON_LIMIT),
                        retention=_retention(env),
                        holdings_limit=_positive(env, HOLDINGS_LIMIT_ENV_VAR, None),
                        payload_bytes=_positive(env, PAYLOAD_BYTES_ENV_VAR, None),
                        candidates_limit=_positive(env, CANDIDATES_LIMIT_ENV_VAR, None))
    return crossing if on else None


def _anchors(env: Mapping[str, str]) -> frozenset[str]:
    """``LABEL_CROSS_ANCHORS``: a comma list of the classifier's label types (unset: the
    person identifiers). The types are the TAG column of the classifier's tag table."""
    raw = env.get(ANCHORS_ENV_VAR) or ""
    if not raw.strip():
        return DEFAULT_ANCHORS
    anchors = frozenset(a.strip().upper() for a in raw.split(",") if a.strip())
    if not anchors:
        raise InvalidCrossing(f"{ANCHORS_ENV_VAR} names no label type: {raw!r}; leave it unset for the person "
                              f"identifiers, or list types of {_TAG_TABLE}")
    unknown = sorted(anchors - set(load_entity_metadata()))
    if unknown:
        raise InvalidCrossing(f"{ANCHORS_ENV_VAR} names {', '.join(map(repr, unknown))}, which the classifier does "
                              f"not label; a label type is one of the TAG column of {_TAG_TABLE}")
    return anchors


def _retention(env: Mapping[str, str]) -> dt.timedelta:
    """``LABEL_CROSS_RETENTION_SECONDS``, checked by building an interval the way the fold
    does (``received + retention``) from now, so one the calendar cannot hold stops the
    processor here, not at the first derivation."""
    seconds = _positive(env, RETENTION_ENV_VAR, int(DEFAULT_RETENTION.total_seconds()))
    try:
        retention = dt.timedelta(seconds=seconds)
        dt.datetime.now(dt.timezone.utc) + retention
    except OverflowError:
        raise InvalidCrossing(f"{RETENTION_ENV_VAR}={seconds} is longer than a date can reach") from None
    return retention


def _positive(env: Mapping[str, str], name: str, default: int | None) -> int | None:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        raise InvalidCrossing(f"{name} must be a positive integer; got {raw!r}")
    return value


@dataclasses.dataclass(frozen=True, slots=True)
class Holding:
    """One labelled value a persistent entity received. ``occurrence`` is the value at
    the received leg (its unit is the origin); ``record`` the extent of the object that
    held it there, ``None`` when it had none; valid strictly inside
    ``(valid_from, valid_to)`` (R1). ``evidence`` is how the leg knew the label:
    ``classified``, ``inherited`` or ``crossed`` (with ``level``)."""

    entity_id: str
    occurrence: Occurrence
    record: tuple[int, int] | None
    valid_from: dt.datetime
    valid_to: dt.datetime
    evidence: str
    level: str | None = None

    @property
    def value(self) -> str:
        return self.occurrence.held

    @property
    def trace_id(self) -> str:
        return self.occurrence.at.trace_id

    def valid_at(self, t: dt.datetime) -> bool:
        return self.valid_from < t < self.valid_to


@dataclasses.dataclass(frozen=True, slots=True)
class Crossed:
    """A crossing asserted at a leaving leg: its level, the holding it rests on, and
    how many other conversations held the value at the payload's time (R2)."""

    level: str
    holding: Holding
    conversations: int


@dataclasses.dataclass(frozen=True, slots=True)
class Carried:
    """How a value is known at a leg it reached by a crossing, here or on its way: the
    strongest level of its crossed origins on that span, and whether there are several
    (``ambiguous``, R3)."""

    level: str
    ambiguous: bool


@dataclasses.dataclass(frozen=True, slots=True)
class Common:
    """A value at a leaving leg held from more than M other conversations: no edge,
    the count (R2)."""

    leg: LegKey
    value: str
    conversations: int


def core(text: str) -> tuple[str, int] | None:
    """*text* from its first letter or digit to its last, and how many words (maximal
    runs of letters and digits) it holds; ``None`` when it holds none. A value found
    bounded in a payload (the exact finder's rule) begins and ends at word boundaries
    there, so its core is a run of that many consecutive words of the payload with the
    text between them: the driver loads a holding by the hash of its core."""
    spans = _word_spans(text)
    return (text[spans[0][0]:spans[-1][1]], len(spans)) if spans else None


def core_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def window_hashes(text: str, longest: int) -> set[str]:
    """The :func:`core_hash` of every run of 1..*longest* consecutive words of *text*
    (from the first word's start to the last word's end): among them is the core of
    every value of at most *longest* words that can be found in *text*. Words of the
    text times *longest* hashes."""
    spans = _word_spans(text)
    return {core_hash(text[start:spans[last][1]])
            for i, (start, _) in enumerate(spans) for last in range(i, min(i + longest, len(spans)))}


def _word_spans(text: str) -> list[tuple[int, int]]:
    out, i, n = [], 0, len(text)
    while i < n:
        if not text[i].isalnum():
            i += 1
            continue
        j = i
        while j < n and text[j].isalnum():
            j += 1
        out.append((i, j))
        i = j
    return out


class Memory:
    """Holdings by entity and value text, with the lengths of the values each entity
    holds: a search tries each length at each start boundary of the payload, so it costs
    (start boundaries × distinct held lengths) dictionary lookups, not one per holding."""

    def __init__(self, holdings: Iterable[Holding] = ()) -> None:
        self._values: dict[str, dict[str, list[Holding]]] = defaultdict(lambda: defaultdict(list))
        self._lengths: dict[str, set[int]] = defaultdict(set)
        for h in holdings:
            self.add(h)

    def add(self, holding: Holding) -> None:
        self._values[holding.entity_id][holding.value].append(holding)
        self._lengths[holding.entity_id].add(len(holding.value))

    def found(self, entity_id: str, text: str, within: int | None = None) -> list[tuple[tuple[int, int], Holding]]:
        """Every holding of *entity_id* whose value is in *text* bounded and whole, by
        the exact finder's rules, with the span it is found at; with *within*, only a
        value that ends inside the first *within* characters (the boundaries are still
        read from the whole text)."""
        values = self._values.get(entity_id)
        if not values:
            return []
        lengths = sorted(self._lengths[entity_id])
        end = len(text) if within is None else min(within, len(text))
        hits = []
        for i in range(end):
            if i and text[i - 1].isalnum():
                continue
            for n in lengths:
                j = i + n
                if j > end:
                    break
                if j < len(text) and text[j].isalnum():
                    continue
                for h in values.get(text[i:j], ()):
                    if is_whole_value(h.occurrence.unit.entity_type, text, i, j):
                        hits.append(((i, j), h))
        return hits


def leaving(memory: Memory, crossing: Crossing, entity_id: str, output: ProjectedPayload, t: dt.datetime,
            routed: Iterable[LegKey], held: int = 0
            ) -> tuple[list[tuple[Occurrence, Crossed]], list[Common], list[str]]:
    """The crossings at *output*, a payload *entity_id* sends at *t*, the common values,
    and the limits that cut the result (:data:`LIMITS`).

    0. The limits: when the entity holds more than its limit (*held*: its occurrences
       valid at *t*, in every conversation), nothing is searched; only the payload's
       first bytes are searched when it is longer than its limit.
    1. Match by text: the holdings found in the payload and valid at *t*. One of this
       conversation's own holdings whose leg the fold already routes here (*routed*)
       is that edge already, never drawn again (R7).
    2. Drop common values: a value held from more than M other conversations valid
       at *t* draws nothing; the count is returned (R2). This conversation's own
       holdings never count.
    3. Assert by origin: the holdings of the remaining values, grouped by the leg and
       record they were received in, under the rule table, unless they are more than
       the candidates limit: then nothing is asserted. One occurrence per
       holding and span, each remembering the holding's occurrence as its source.
    """
    if crossing.holdings_limit is not None and held > crossing.holdings_limit:
        return [], [], [HOLDINGS_PER_ENTITY]
    cuts, within = [], None
    if crossing.payload_bytes is not None and len(output.text.encode()) > crossing.payload_bytes:
        within = len(output.text.encode()[:crossing.payload_bytes].decode(errors="ignore"))
        cuts.append(PAYLOAD_BYTES)
    trace_id = output.site.trace_id
    skip = set(routed)
    hits = [(span, h) for span, h in memory.found(entity_id, output.text, within) if h.valid_at(t)
            and not (h.trace_id == trace_id and h.occurrence.at.leg in skip)]
    held_from: dict[str, set[str]] = defaultdict(set)
    for _, h in hits:
        if h.trace_id != trace_id:
            held_from[h.value].add(h.trace_id)
    count = {v: len(traces) for v, traces in held_from.items()}
    common = sorted(v for v, n in count.items() if n > crossing.common_limit)
    commons = [Common(output.site.leg, v, count[v]) for v in common]
    candidates = [(span, h) for span, h in hits if h.value not in common]
    if crossing.candidates_limit is not None and len(candidates) > crossing.candidates_limit:
        return [], commons, cuts + [CANDIDATES_PER_PAYLOAD]

    def anchors(h: Holding) -> bool:
        return h.occurrence.unit.entity_type in crossing.anchors

    in_record: dict[tuple, list[tuple[tuple[int, int], Holding]]] = defaultdict(list)
    asserted = []
    for span, h in candidates:
        if h.record is not None:
            in_record[(h.trace_id, h.occurrence.at.leg, h.record)].append((span, h))
        elif anchors(h):
            asserted.append((span, h, VALUE_ONLY))
    for members in in_record.values():
        if not any(anchors(h) for _, h in members):
            continue
        if len({h.value for _, h in members}) > 1:
            asserted += [(span, h, CORROBORATED) for span, h in members]
        else:
            asserted += [(span, h, VALUE_ONLY) for span, h in members if anchors(h)]
    out = []
    for span, h, level in asserted:
        o = h.occurrence
        occ = Occurrence(unit=o.unit, at=output.site, span=span, held_from=o.held_from, held_to=o.held_to,
                         matched_text=output.text[span[0]:span[1]], source=o)
        out.append((occ, Crossed(level, h, count.get(h.value, 0))))
    return out, commons, cuts


def records(text: str) -> dict[tuple[int, int], tuple[int, int]]:
    """In the serialised JSON *text* of a structured payload, each scalar member of
    an object (a string's characters between its quotes, or a number) mapped to the
    extent of that object: a value has a record only when it is the whole of such a
    member (R4). A key, an array element, part of a longer string — prose, or JSON
    carried inside a string — has none: nothing is read inside a string."""
    out: dict[tuple[int, int], tuple[int, int]] = {}
    stack: list[tuple[str, int, list[tuple[int, int]]]] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            k = j + 1
            while k < n and text[k].isspace():
                k += 1
            if stack and stack[-1][0] == "{" and not (k < n and text[k] == ":"):
                stack[-1][2].append((i + 1, j))
            i = j + 1
        elif c in "{[":
            stack.append((c, i, []))
            i += 1
        elif c in "}]":
            if stack:
                kind, start, members = stack.pop()
                if kind == "{":
                    out.update((m, (start, i + 1)) for m in members)
            i += 1
        elif c == "-" or c.isdigit():
            j = i + 1
            while j < n and text[j] not in ",}] \n\t":
                j += 1
            if stack and stack[-1][0] == "{":
                stack[-1][2].append((i, j))
            i = j
        else:
            i += 1
    return out
