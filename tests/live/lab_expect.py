"""The expected forest of a lineage_lab plan — the wire contract as a function.

Given the plan the driver sends and the fleet, walk the tree the way the
sidecars and the derivation will see it:

- the entry: demo-client → lab-a (a2a), the root;
- an ``mcp`` step by agent X on tool T with a per-turn session: X → T (mcp)
  interactions under X's inbound — exactly one ``tools/call`` (the tool
  name is the op), plus the session's lifecycle exchanges (``initialize``,
  ``notifications/initialized``, …), whose count the MCP client decides:
  they are expected as "one or more X → T mcp lifecycle" and recorded;
  with ``pod_lifetime`` the call is NOT in the tree (it rides the startup
  context: its own trace) — an absence that is asserted;
- an ``a2a`` step: X → Y (a2a message/send) under X's inbound, and the
  sub-plan's steps under Y's inbound (the echo);
- an ``http`` step: X → host (mcp — the body is MCP-shaped) under X's
  inbound; the callee is named by the Host header, tool kind, no echo;
- an ``llm`` step: X → llm (inference) under X's inbound;
- ``parallel`` / ``loop``: the same rules, order-free / repeated;
- ``set``/``sleep``/``fail``: no wire.

Equality is asserted on the multiset of (caller, callee, protocol, op) edges
(op = tool name for tools/call, the a2a method, the model for inference,
``lifecycle`` for the rest), on the parent of each, and on the entity set.
Lifecycle exchanges are compared by presence per (caller, callee), not count.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class Expected:
    calls: Counter = field(default_factory=Counter)       # (caller, callee, proto, op) -> count
    lifecycle: set = field(default_factory=set)           # (caller, callee) pairs with mcp lifecycle exchanges
    absent: list = field(default_factory=list)            # pod_lifetime calls: (caller, callee, tool) not in the tree
    parents: dict = field(default_factory=dict)           # (caller, callee, proto, op) -> caller of the parent interaction
    entities: set = field(default_factory=set)

    @property
    def depth(self) -> int:
        # root = demo-client→lab-a (0); every call under lab-a is 1; under a peer 2 …
        d = {("demo-client", "lab-a"): 0}
        changed = True
        while changed:
            changed = False
            for (caller, callee, _p, _o), parent_caller in self.parents.items():
                base = next((v for (pc, pe), v in d.items() if pe == caller), None)
                if base is not None and d.get((caller, callee), -1) < base + 1:
                    d[(caller, callee)] = base + 1
                    changed = True
        return max(d.values(), default=0)


def tool_pod(fn_name: str) -> str:
    return fn_name.replace("_", "-")


def expected_forest(plan: dict, fleet, *, entry: str = "demo-client", first_agent: str = "lab-a") -> Expected:
    e = Expected()
    e.calls[(entry, first_agent, "a2a", "message/send")] += 1
    e.parents[(entry, first_agent, "a2a", "message/send")] = None
    e.entities |= {entry, first_agent}
    _walk(plan.get("steps") or [], first_agent, entry, e, fleet)
    return e


def _walk(steps: list[dict], agent: str, parent_caller: str, e: Expected, fleet) -> None:
    for st in steps:
        op = st.get("op")
        if op == "mcp":
            tool = tool_pod(st["tool"])
            if st.get("session", "per_turn") == "pod_lifetime":
                e.absent.append((agent, tool, st["tool"]))
                continue
            key = (agent, tool, "mcp", st["tool"])
            e.calls[key] += 1
            e.parents[key] = parent_caller
            e.lifecycle.add((agent, tool))
            e.entities |= {agent, tool}
        elif op == "a2a":
            peer = st["agent"]
            key = (agent, peer, "a2a", "message/send")
            e.calls[key] += 1
            e.parents[key] = parent_caller
            e.entities |= {agent, peer}
            _walk((st.get("plan") or {}).get("steps") or [], peer, agent, e, fleet)
        elif op == "http":
            host = (st.get("host") or st["url"].split("//", 1)[1].split("/", 1)[0]).split(":")[0]
            key = (agent, host, "mcp", st.get("tool", "receive"))
            e.calls[key] += 1
            e.parents[key] = parent_caller
            e.entities |= {agent, host}
        elif op == "llm":
            key = (agent, fleet.llm, "inference", fleet.llm.split("/", 1)[1])
            e.calls[key] += 1
            e.parents[key] = parent_caller
            e.entities |= {agent, fleet.llm}
        elif op == "parallel":
            for branch in st.get("branches") or []:
                _walk(branch, agent, parent_caller, e, fleet)
        elif op == "loop":
            for _ in range(int(st.get("n", 1))):
                _walk(st.get("steps") or [], agent, parent_caller, e, fleet)
        elif op in ("set", "sleep", "fail"):
            pass  # no wire
        else:
            # a typo in a plan must not silently drop an expectation; the
            # runtime rejects the same op with the same words
            raise ValueError(f"unknown plan op {op!r}")


# --- what the tables hold, in the same vocabulary ---------------------------------

LIFECYCLE_METHODS = {"initialize", "ping", "tools/list", "resources/list", "prompts/list",
                     "resources/templates/list"}


def observed(dg, trace_id: str, fleet) -> dict:
    """The derived interactions of the trace as (caller, callee, proto, op)
    with their parent's caller, from the anchor spans' facts."""
    from tests.live.fleet import name_of
    rows = dg.q("""
        SELECT i.id, i.parent_interaction_id, ce.kind::text, ce.natural_key, ee.kind::text, ee.natural_key,
               a.attributes->>'lineage.protocol', a.attributes->>'mcp.method', a.attributes->>'mcp.tool',
               a.attributes->>'a2a.method', a.attributes->>'inference.model', a.attributes->>'lineage.peer.host'
        FROM interactions i
        JOIN entities ce ON ce.id = i.caller_entity_id JOIN entities ee ON ee.id = i.callee_entity_id
        JOIN interaction_spans s ON s.interaction_id = i.id AND s.role = 'anchor'
        JOIN spans a ON a.span_id = s.span_id AND a.trace_id = i.trace_id
        WHERE i.trace_id = %s""", (trace_id,))
    by_id = {r[0]: r for r in rows}
    calls, lifecycle, parents, entities = Counter(), set(), {}, set()
    for iid, pid, ck, ckey, ek, ekey, proto, mm, mt, am, model, peer in rows:
        caller, callee = name_of(ck, ckey), name_of(ek, ekey)
        entities |= {caller, callee}
        if proto == "mcp" and mm != "tools/call":
            lifecycle.add((caller, callee))
            continue
        if proto == "mcp":
            op = mt
        elif proto == "a2a":
            op = "message/send" if (am or "").startswith("message/") else am
        elif proto == "inference":
            op = model
        else:
            op = None
        key = (caller, callee, proto, op)
        calls[key] += 1
        parent = by_id.get(pid)
        parents[key] = name_of(parent[2], parent[3]) if parent else None
    return {"calls": calls, "lifecycle": lifecycle, "parents": parents, "entities": entities}


def diff(exp: Expected, obs: dict) -> dict:
    missing = {k: v for k, v in exp.calls.items() if obs["calls"].get(k, 0) < v}
    extra = {k: v for k, v in obs["calls"].items() if exp.calls.get(k, 0) < v}
    missing_lc = exp.lifecycle - obs["lifecycle"]
    extra_lc = obs["lifecycle"] - exp.lifecycle
    wrong_parent = {k: (exp.parents.get(k), obs["parents"].get(k)) for k in exp.calls
                    if k in obs["parents"] and exp.parents.get(k) != obs["parents"].get(k)}
    return {"missing_calls": {str(k): v for k, v in missing.items()}, "extra_calls": {str(k): v for k, v in extra.items()},
            "missing_lifecycle": sorted(map(str, missing_lc)), "extra_lifecycle": sorted(map(str, extra_lc)),
            "wrong_parent": {str(k): v for k, v in wrong_parent.items()},
            "missing_entities": sorted(exp.entities - obs["entities"]), "extra_entities": sorted(obs["entities"] - exp.entities),
            "equal": not (missing or extra or missing_lc or extra_lc or wrong_parent or exp.entities != obs["entities"])}
