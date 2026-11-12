"""Render a case line to sidecar span rows (the ``spans``-table row shape that
``tools/load_trace.py`` replays over the OTLP wire).

Every ``X>Y[...]`` is one wire payload. ``Y>X[...]`` answers the open ``X>Y``
call (request, response). The renderer puts on the wire exactly the payloads the
line writes, nothing more.

Nothing is guessed: a request belongs to the one call its sender is serving (or
the one ``T1(A1)>V1`` names), and the renderer refuses a line where that is not
exactly one call (only the user sends a request serving none), a response with
no open request (a payload to the user, from an LLM or a partner, or
from a tool to an agent is always a response), a response with two, a
conversation that does not start with the user, a request the conversation never
answers, ⚑ on a response, a flagged request whose response carries a unit (the
verdict is the flagged exchange's risk record), two units with equal bytes, and
two values under one JSON key.

Sidecars: agents and tools have one (both sides of an agent-tool call are
emitted, caller CLIENT spans and callee SERVER echoes, as live clusters do);
users, LLMs and external partners have none.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
from typing import Any

from . import caseline as cl

NAMESPACE = "lab"
T0 = dt.datetime(2026, 11, 8, 9, 0, 0, tzinfo=dt.timezone.utc)

# One value per label; a second unit with the same label takes the next value. NAME and PN share names: a
# unit the model may find as either is declared ``[NAME, PN]``, and two units never travel as equal bytes.
VALUES: dict[str, list[str]] = {
    "MRN": ["4471820", "5530917", "6618204"],
    "NAME": ["Rosa Alvarez", "Daniel Okafor", "Mei Lin"],
    "PN": ["Rosa Alvarez", "Daniel Okafor", "Mei Lin"],
    "PHONE": ["+1 415 555 0134", "+1 212 555 0188"],
    "SSN": ["123-45-6789", "987-65-4321"],
    "DOB": ["1961-03-14", "1978-11-02"],
    "DATE": ["2026-10-21", "2026-10-22"],
    "DRUG": ["Metformin", "Ozempic"],
    "DOSE": ["500 mg", "1 mg"],
    "DIS": ["type 2 diabetes", "hypertension"],
    "EMAIL": ["rosa.alvarez@example.net"],
    "ADDRESS": ["12 Mission St, San Francisco CA 94103"],
    "NOTE": ["Patient reports stable glucose readings; continue current regimen and recheck A1c in three months."],
    "PV": ["2025-11-25"],
    "ORDER": ["7719302"],
}
KEYS: dict[str, str] = {
    "MRN": "mrn", "NAME": "name", "PN": "name", "PHONE": "phone", "SSN": "ssn", "DOB": "dob",
    "DATE": "date", "DRUG": "drug", "DOSE": "dose", "DIS": "diagnosis", "EMAIL": "email",
    "ADDRESS": "address", "NOTE": "note", "PV": "protocolVersion", "ORDER": "order_id",
}
FILLER = [
    "routine refill request received",
    "please process at the usual priority",
    "no further action needed",
    "forwarded for fulfilment",
    "acknowledged",
    "batch job window 02:00",
]


@dataclasses.dataclass(frozen=True)
class Rendered:
    """One line leg as it went on the wire."""
    leg: cl.Leg
    trace_id: str
    request_span_id: str      # the exchange's request span (caller side)
    role: str                 # "request" | "response": which leg of the exchange this payload is
    content: dict[str, Any]
    text_values: dict[str, str]   # unit name -> the rendered value


def assign_values(case_id: str, units: dict[str, str]) -> dict[str, str]:
    """unit -> its bytes on the wire. Two units with equal bytes are refused: edges and findings are tied to a
    unit by its bytes."""
    taken: dict[str, int] = {}
    out: dict[str, str] = {}
    for unit, label in units.items():
        i = taken.get(label, 0)
        if i >= len(VALUES[label]):
            raise ValueError(f"{case_id}: more than {len(VALUES[label])} units of {label}; add values")
        value = VALUES[label][i]
        twin = next((u for u, v in out.items() if v == value), None)
        if twin:
            raise ValueError(f"{case_id}: {twin} and {unit} would travel as the same bytes {value!r}; "
                             f"declare one unit with both types ([NAME, PN])")
        out[unit] = value
        taken[label] = i + 1
    return out


class Renderer:
    def __init__(self, case_id: str, units: dict[str, str]) -> None:
        self.case_id = case_id
        self.units = units                      # s1 -> "MRN"
        self.values = assign_values(case_id, units)
        self.rows: list[dict[str, Any]] = []
        self.rendered: list[Rendered] = []
        self._n = 0

    def content_of(self, leg: cl.Leg) -> tuple[dict[str, Any], dict[str, str]]:
        body: dict[str, Any] = {}
        text_values: dict[str, str] = {}
        fill = 0

        def put(key: str, value: str) -> None:
            if key.startswith("note"):
                raise ValueError(f"{self.case_id} leg {leg.index}: key {key!r} is reserved for filler")
            if key in body:
                raise ValueError(f"{self.case_id} leg {leg.index}: two values under the key {key!r}; give one an @key")
            body[key] = value

        for e in leg.elements:
            if e.is_filler:
                body[f"note{fill or ''}"] = FILLER[(leg.index + fill) % len(FILLER)]
                fill += 1
            else:
                if e.name not in self.units:
                    raise ValueError(f"{self.case_id} leg {leg.index}: unit {e.name} is not declared in units")
                put(e.key or KEYS[self.units[e.name]], self.values[e.name])
                text_values[e.name] = self.values[e.name]
        return body, text_values

    # --- ids ---------------------------------------------------------------
    def _id(self, what: str) -> str:
        self._n += 1
        return hashlib.sha1(f"{self.case_id}/{what}/{self._n}".encode()).hexdigest()[:16]

    def _trace_id(self, c: int) -> str:
        return hashlib.sha1(f"{self.case_id}/trace/{c}".encode()).hexdigest()[:32]

    # --- spans -------------------------------------------------------------
    def _row(self, *, trace_id: str, span_id: str, parent: str | None, kind: str, at: dt.datetime,
             self_id: str, attrs: dict[str, Any]) -> None:
        a = {"lineage.self.id": self_id, "lineage.self.namespace": NAMESPACE, **attrs}
        ts = at.isoformat()
        self.rows.append({
            "trace_id": trace_id, "span_id": span_id, "parent_id": parent, "kind": kind,
            "name": self_id, "service_name": "authbridge",
            "started_at": ts, "ended_at": ts, "error": False, "attributes": a,
        })

    @staticmethod
    def _protocol(frm: str, to: str) -> str:
        k2 = cl.kind_of(to)
        if k2 == "llm":
            return "inference"
        if k2 == "tool":
            return "mcp"
        if k2 == "external":
            return "http"
        return "a2a"   # agent to agent, user to agent

    @staticmethod
    def _sidecar(party: str) -> bool:
        return cl.kind_of(party) in ("agent", "tool")

    def _peer_host(self, to: str) -> str:
        k = cl.kind_of(to)
        if k == "external":
            return f"api.{to.lower()}.partner.example:8000"
        if k == "llm":
            return "host.containers.internal:11434"
        return f"{to.lower()}.{NAMESPACE}.svc:8000"

    @dataclasses.dataclass
    class _Open:
        leg: cl.Leg
        trace_id: str
        frm: str
        to: str
        req_id: str
        echo_id: str | None
        protocol: str
        serving: "Renderer._Open | None" = None   # the call this request was sent inside

    def _proto_attrs(self, protocol: str, to: str) -> dict[str, Any]:
        if protocol == "mcp":
            return {"mcp.method": "tools/call", "mcp.tool": to.lower()}
        if protocol == "a2a":
            return {"a2a.method": "message/send"}
        if protocol == "inference":
            return {"inference.model": "qwen2.5:7b"}
        return {"http.method": "POST", "url.path": "/intake", "url.scheme": "http"}

    def _serving(self, leg: cl.Leg, open_: list["Renderer._Open"]) -> "Renderer._Open | None":
        """The one open call the sender of *leg* is serving: its request belongs to that call."""
        calls = [ex for ex in open_ if ex.to == leg.frm and (leg.for_call is None or ex.frm == leg.for_call)]
        if leg.for_call and len(calls) != 1:
            raise ValueError(f"{self.case_id} leg {leg.index}: {len(calls)} open calls from {leg.for_call} on {leg.frm}")
        if len(calls) > 1:
            raise ValueError(f"{self.case_id} leg {leg.index}: {leg.frm} is serving {len(calls)} calls "
                             f"({', '.join(ex.frm for ex in calls)}); name the one this request belongs to, "
                             f"{leg.frm}(caller)>{leg.to}")
        return calls[0] if calls else None

    def open_exchange(self, *, trace_id: str, leg: cl.Leg, frm: str, to: str, req: dict[str, Any],
                      at: dt.datetime, parent: str | None) -> "_Open":
        """Emit the request half of one exchange frm -> to (caller CLIENT span, callee
        SERVER echo). The response half is emitted by :meth:`close_exchange` when the
        answer appears on the line, so nested calls sit between request and response."""
        protocol = self._protocol(frm, to)
        caller_side, callee_side = self._sidecar(frm), self._sidecar(to)
        req_id = self._id("req")
        proto_attrs = self._proto_attrs(protocol, to)
        if caller_side:
            self._row(trace_id=trace_id, span_id=req_id, parent=parent, kind="CLIENT", at=at, self_id=frm.lower(),
                      attrs={"lineage.role": "request", "lineage.direction": "outbound", "lineage.protocol": protocol,
                             "lineage.exchange.id": req_id, "lineage.peer.host": self._peer_host(to),
                             **proto_attrs, "input.value": req})
        echo_id = None
        if callee_side:
            echo_id = self._id("echo")
            inbound_attrs: dict[str, Any] = {}
            if cl.kind_of(frm) == "user":
                inbound_attrs["lineage.principal.sub"] = "alice"
            self._row(trace_id=trace_id, span_id=echo_id, parent=req_id if caller_side else parent, kind="SERVER",
                      at=at + dt.timedelta(milliseconds=100), self_id=to.lower(),
                      attrs={"lineage.role": "request", "lineage.direction": "inbound", "lineage.protocol": protocol,
                             "lineage.exchange.id": echo_id, "lineage.peer.host": self._peer_host(to),
                             **proto_attrs, **inbound_attrs, "input.value": req})
        return self._Open(leg, trace_id, frm, to, req_id if caller_side else (echo_id or req_id), echo_id, protocol)

    def close_exchange(self, ex: "_Open", resp: dict[str, Any], at: dt.datetime) -> None:
        caller_side, callee_side = self._sidecar(ex.frm), self._sidecar(ex.to)
        if callee_side and ex.echo_id:
            self._row(trace_id=ex.trace_id, span_id=self._id("echo-resp"), parent=ex.echo_id, kind="SERVER",
                      at=at, self_id=ex.to.lower(),
                      attrs={"lineage.role": "response", "lineage.direction": "inbound", "lineage.protocol": ex.protocol,
                             "lineage.exchange.id": ex.echo_id, "lineage.outcome": "ok", "output.value": resp})
        if caller_side:
            self._row(trace_id=ex.trace_id, span_id=self._id("resp"), parent=ex.req_id, kind="CLIENT",
                      at=at + dt.timedelta(milliseconds=100), self_id=ex.frm.lower(),
                      attrs={"lineage.role": "response", "lineage.direction": "outbound", "lineage.protocol": ex.protocol,
                             "lineage.exchange.id": ex.req_id, "lineage.outcome": "ok", "output.value": resp})

    def render(self, case: cl.CaseLine) -> list[dict[str, Any]]:
        at = T0
        for c, conv in enumerate(case.conversations):
            trace_id = self._trace_id(c)
            at = at + dt.timedelta(seconds=conv.gap_seconds)
            open_: list[Renderer._Open] = []
            legs = list(conv.legs)
            if not legs or legs[0].invisible or cl.kind_of(legs[0].frm) != "user":
                raise ValueError(f"{self.case_id}: a conversation starts with the user (U>...); write its first request")
            for leg in legs:
                if leg.invisible:
                    continue
                body, values = self.content_of(leg)
                answering = [ex for ex in open_ if ex.frm == leg.to and ex.to == leg.frm]
                if len(answering) > 1:
                    raise ValueError(f"{self.case_id} leg {leg.index}: {leg.label()} could answer {len(answering)} "
                                     f"open calls {leg.to}>{leg.frm}")
                if answering:
                    if leg.for_call:
                        raise ValueError(f"leg {leg.index}: (caller) belongs on a request, {leg.label()} is a response")
                    if leg.sink:
                        raise ValueError(f"{self.case_id} leg {leg.index}: ⚑ marks a request; {leg.label()} is a response")
                    ex = answering[0]
                    pending_calls = [o for o in open_ if o.serving is ex]
                    if pending_calls:
                        raise ValueError(f"{self.case_id} leg {leg.index}: {leg.frm} answers while its call "
                                         f"{pending_calls[-1].leg.index} {pending_calls[-1].leg.label()} is still open")
                    if ex.leg.sink and leg.units:
                        raise ValueError(f"{self.case_id} leg {leg.index}: the flagged exchange's response carries "
                                         f"{' '.join(leg.units)}; the verdict is the exchange's risk record, so it "
                                         f"must rest on the request alone")
                    open_.remove(ex)
                    self.close_exchange(ex, body, at)
                    self.rendered.append(Rendered(leg, trace_id, ex.req_id, "response", body, values))
                elif _answer_only(leg):
                    raise ValueError(f"{self.case_id} leg {leg.index}: {leg.label()} answers no open request; "
                                     f"write the request {leg.to}>{leg.frm} before it")
                else:
                    call = self._serving(leg, open_)
                    if call is None and cl.kind_of(leg.frm) != "user":
                        raise ValueError(f"{self.case_id} leg {leg.index}: {leg.frm} sends {leg.label()} while serving "
                                         f"no call; only the user starts one")
                    ex = self.open_exchange(trace_id=trace_id, leg=leg, frm=leg.frm, to=leg.to, req=body, at=at,
                                            parent=call.echo_id if call else None)
                    ex.serving = call
                    open_.append(ex)
                    self.rendered.append(Rendered(leg, trace_id, ex.req_id, "request", body, values))
                at = at + dt.timedelta(seconds=2)
            if open_:
                raise ValueError(f"{self.case_id}: requests the conversation never answers: "
                                 + ", ".join(f"{ex.leg.index} {ex.leg.label()}" for ex in open_)
                                 + "; write each answer on the line")
        return self.rows


def _answer_only(leg: cl.Leg) -> bool:
    """A payload that can only be a response: to the user, from a party that never calls (an LLM, a partner),
    or from a tool to an agent."""
    frm, to = cl.kind_of(leg.frm), cl.kind_of(leg.to)
    return to == "user" or frm in ("llm", "external") or (frm == "tool" and to == "agent")


def render(case_id: str, units: dict[str, str], case: cl.CaseLine) -> Renderer:
    r = Renderer(case_id, units)
    r.render(case)
    return r


def payload_texts(renderer: Renderer) -> dict[str, dict[str, Any]]:
    """Every distinct payload body the case puts on the wire, keyed by its projected text."""
    from data_governance.processors.classification import projection

    out: dict[str, dict[str, Any]] = {}
    for row in renderer.rows:
        a = row["attributes"]
        body = a.get("input.value") if "input.value" in a else a.get("output.value")
        if body is None:
            continue
        out[projection.project(body)] = body
    return out


def dumps(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, indent=1, default=str)
