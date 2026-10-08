"""expected_forest is the contract as a function: check it on the catalogue's shapes."""
from pathlib import Path

from tests.live import fleet as fm
from tests.live.lab_expect import expected_forest

FLEET = fm.load(Path(__file__).parent.parent / "live" / "apps" / "lineage_lab.yaml")
LLM = FLEET.llm


def test_forward_to_external_partner_r2():
    plan = {"mark": "m0", "steps": [
        {"op": "mcp", "tool": "lab_store", "args": {"op": "read", "key": "alice"}, "mark": "rec"},
        {"op": "a2a", "agent": "lab-b", "plan": {"steps": [
            {"op": "http", "url": "http://lab-partner:8000/mcp", "host": "api.partner.example:8000",
             "args": {"payload": "$rec"}, "mark": "sent"}]}, "mark": "b"}]}
    e = expected_forest(plan, FLEET)
    assert e.calls == {("demo-client", "lab-a", "a2a", "message/send"): 1, ("lab-a", "lab-store", "mcp", "lab_store"): 1,
                       ("lab-a", "lab-b", "a2a", "message/send"): 1, ("lab-b", "api.partner.example", "mcp", "receive"): 1}
    assert e.lifecycle == {("lab-a", "lab-store")}
    assert e.parents[("lab-b", "api.partner.example", "mcp", "receive")] == "lab-a"
    assert e.entities == {"demo-client", "lab-a", "lab-store", "lab-b", "api.partner.example"}
    assert e.depth == 2 and e.absent == []


def test_pod_lifetime_session_is_an_asserted_absence():
    plan = {"steps": [{"op": "mcp", "tool": "lab_tool_x", "args": {}, "session": "pod_lifetime"}]}
    e = expected_forest(plan, FLEET)
    assert e.calls == {("demo-client", "lab-a", "a2a", "message/send"): 1}
    assert e.absent == [("lab-a", "lab-tool-x", "lab_tool_x")]


def test_parallel_loop_llm_r10_shape():
    sub = lambda key: {"steps": [{"op": "mcp", "tool": "lab_store", "args": {"op": "read", "key": key}, "mark": "r"},
                                 {"op": "mcp", "tool": "lab_tool_y", "args": {"op": "forward", "payload": "$r"}, "mark": "t"}]}
    plan = {"steps": [{"op": "parallel", "branches": [[{"op": "a2a", "agent": "lab-b", "plan": sub("alice"), "mark": "b"}],
                                                      [{"op": "a2a", "agent": "lab-c", "plan": sub("bob"), "mark": "c"}]]},
                      {"op": "loop", "n": 2, "steps": [{"op": "http", "url": "http://lab-partner:8000/mcp",
                                                        "host": "api.partner.example:8000", "args": {"p": 1}}]},
                      {"op": "llm", "prompt": "x"}]}
    e = expected_forest(plan, FLEET)
    assert e.calls[("lab-a", "api.partner.example", "mcp", "receive")] == 2
    assert e.calls[("lab-b", "lab-tool-y", "mcp", "lab_tool_y")] == 1 and e.calls[("lab-c", "lab-store", "mcp", "lab_store")] == 1
    assert e.calls[("lab-a", LLM, "inference", "qwen2.5:7b")] == 1
    assert e.parents[("lab-b", "lab-store", "mcp", "lab_store")] == "lab-a"
    assert e.depth == 2


def test_unknown_op_is_refused():
    import pytest
    with pytest.raises(ValueError):
        expected_forest({"steps": [{"op": "teleport"}]}, FLEET)
