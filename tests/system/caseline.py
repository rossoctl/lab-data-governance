"""The case line: one line of text, the conversation a case puts on the wire.

    U>A[-], A>T1[-], T1>A[s1 s2 s3 -], A>T2[s1@patient_ref -], T2>V[s1@external_id -]⚑, V>T2[-], T2>A[-], A>U[-]

- ``X>Y[...]`` is one wire payload sent by X to Y. Party letters give the kind:
  ``U`` user, ``A`` agent, ``T`` tool, ``L`` LLM, ``V`` external partner.
  A digit suffix distinguishes parties of one kind.
- Inside ``[...]``: ``s1`` a sensitive unit (its label is declared once per case),
  ``-`` some content nobody cares about, ``s1@key`` the JSON key the value travels
  under (by default the label's own key: ``ssn``, ``mrn``, ``dob`` ...).
- ``T1(A1)>V1[...]``: the request T1 sends belongs to the call A1 opened on T1 (the wire
  parent). Without it the sender must be serving exactly one call; the renderer refuses the line otherwise.
- ``,`` the next payload in the same conversation; ``..V1..`` an invisible hop
  through V1 (two payloads, there and back, so the numbering stays the trunk's); ``⚑`` after a payload marks the egress the verdict is read at;
  ``~>`` (or ``~10min>``) a later conversation.
"""

from __future__ import annotations

import dataclasses
import re

PARTY_KINDS = {"U": "user", "A": "agent", "T": "tool", "L": "llm", "V": "external"}


@dataclasses.dataclass(frozen=True)
class Element:
    name: str            # "s1", "-"
    key: str | None      # JSON key override

    @property
    def is_unit(self) -> bool:
        return self.name != "-"

    @property
    def is_filler(self) -> bool:
        return self.name == "-"


@dataclasses.dataclass(frozen=True)
class Leg:
    index: int                      # 1-based across the whole line
    frm: str                        # "A", "T1", ...
    to: str
    elements: tuple[Element, ...]
    sink: bool = False
    invisible: bool = False         # "..P..": a hop the wire never sees; counts as two payloads (index, index+1)
    for_call: str | None = None     # "T1(A1)>V1": the request belongs to the call A1 opened on T1

    @property
    def units(self) -> tuple[str, ...]:
        return tuple(e.name for e in self.elements if e.is_unit)

    def label(self) -> str:
        return f"{self.frm}>{self.to}"


@dataclasses.dataclass(frozen=True)
class Conversation:
    legs: tuple[Leg, ...]
    gap_seconds: int = 0            # time after the previous conversation


@dataclasses.dataclass(frozen=True)
class CaseLine:
    conversations: tuple[Conversation, ...]

    @property
    def legs(self) -> list[Leg]:
        return [leg for c in self.conversations for leg in c.legs]

    @property
    def sink(self) -> Leg | None:
        flagged = [leg for leg in self.legs if leg.sink]
        return flagged[-1] if flagged else None

    @property
    def crosses_conversations(self) -> bool:
        return len(self.conversations) > 1


_GAP = re.compile(r"~(?:(\d+)(s|min|h))?>")
_LEG = re.compile(r"^\s*(?:([A-Z]\d*)(?:\(([A-Z]\d*)\))?>([A-Z]\d*))?\s*\[([^\]]*)\]\s*(⚑)?\s*$")
_INVISIBLE = re.compile(r"^\s*\.\.\s*([A-Z]\d*)\s*\.\.\s*$")


def kind_of(party: str) -> str:
    if party[0] not in PARTY_KINDS:
        raise ValueError(f"unknown party {party!r}: letters are {sorted(PARTY_KINDS)}")
    return PARTY_KINDS[party[0]]


def _gap_seconds(num: str | None, unit: str | None) -> int:
    if num is None:
        return 600
    n = int(num)
    return n * {"s": 1, "min": 60, "h": 3600}[unit]


def _element(token: str) -> Element:
    key = None
    if "@" in token:
        token, key = token.split("@", 1)
    if token == "-":
        return Element("-", None)
    if not re.fullmatch(r"s\d+", token):
        raise ValueError(f"bad element {token!r}")
    return Element(token, key)


def _split_legs(text: str) -> list[str]:
    # commas inside [...] never occur (elements are space-separated), so a plain split is safe
    return [part for part in (p.strip() for p in text.split(",")) if part]


def parse(line: str) -> CaseLine:
    conversations: list[Conversation] = []
    index = 0
    pieces = _GAP.split(line)
    # re.split with two groups yields: text, num, unit, text, num, unit, ...
    texts = pieces[0::3]
    gaps = list(zip(pieces[1::3], pieces[2::3]))
    for c, text in enumerate(texts):
        legs: list[Leg] = []
        last_to: str | None = None
        for raw in _split_legs(text):
            inv = _INVISIBLE.match(raw)
            if inv:
                index += 1
                legs.append(Leg(index, last_to or "?", inv.group(1), (), invisible=True))
                index += 1          # the hop is two payloads (there and back): it keeps the trunk's numbering
                last_to = inv.group(1)
                continue
            m = _LEG.match(raw)
            if not m:
                raise ValueError(f"bad leg {raw!r}")
            frm, for_call, to, body, flag = m.groups()
            if frm is None or to is None:
                raise ValueError(f"leg needs parties: {raw!r}")
            index += 1
            elements = tuple(_element(t) for t in body.split())
            legs.append(Leg(index, frm, to, elements, sink=flag is not None, for_call=for_call))
            last_to = to
        gap = 0 if c == 0 else _gap_seconds(*gaps[c - 1])
        conversations.append(Conversation(tuple(legs), gap))
    return CaseLine(tuple(conversations))

