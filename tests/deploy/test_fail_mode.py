"""deploy/fail_mode.py: the fail-mode document's validator and the rows it renders for the Sentry gate (#342)."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "deploy" / "fail_mode.py"

GOOD = {"rows": [{"destination": "peer.example", "fallback": "allow"},
                 {"destination": "slow.example", "wait_ms": 3000},
                 {"fallback": "deny", "wait_ms": 1, "destination": "Edge.Example"}]}


def run(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, capture_output=True, text=True, timeout=30)


def test_a_good_document_validates_and_renders_the_gate_rows(tmp_path) -> None:
    """D2: a good document exits 0; render gives each row with only its keys, in destination, wait_ms, fallback order."""
    doc = tmp_path / "doc.json"
    doc.write_text(json.dumps(GOOD))
    before = doc.read_bytes()
    result = run("validate", str(doc))
    assert result.returncode == 0 and result.stderr == "", result.stderr
    assert "3 rows" in result.stdout
    assert doc.read_bytes() == before
    rendered = run("render", stdin=json.dumps(GOOD))
    assert rendered.returncode == 0, rendered.stderr
    assert rendered.stdout == json.dumps([{"destination": "peer.example", "fallback": "allow"},
                                          {"destination": "slow.example", "wait_ms": 3000},
                                          {"destination": "Edge.Example", "wait_ms": 1, "fallback": "deny"}])
    assert run("render", stdin='{"rows": []}').stdout == "[]"


@pytest.mark.parametrize(("text", "rule"), [
    ("{", "not JSON"),
    ('{"rows": [], "version": 1}', 'the document is {"rows": [...]}, one key'),
    ('[{"destination": "peer.example", "fallback": "deny"}]', 'the document is {"rows": [...]}, one key'),
    ('{"rows": {"destination": "peer.example"}}', "rows is a list"),
    ('{"rows": ["peer.example"]}', "a row is an object"),
    ('{"rows": [{"destination": "peer.example", "fallback": "deny", "source": "a"}]}', "unknown key 'source'"),
    ('{"rows": [{"fallback": "deny"}]}', "a row names a destination"),
    ('{"rows": [{"destination": "", "fallback": "deny"}]}', "an empty destination"),
    ('{"rows": [{"destination": 7, "fallback": "deny"}]}', "an empty destination"),
    ('{"rows": [{"destination": "peer.example:8443", "fallback": "deny"}]}', "a destination has no port"),
    ('{"rows": [{"destination": "https://peer.example", "fallback": "deny"}]}', "a destination has no scheme"),
    ('{"rows": [{"destination": ".", "fallback": "deny"}]}', "an empty destination"),
    ('{"rows": [{"destination": "peer .example", "fallback": "deny"}]}', "a destination has no whitespace"),
    ('{"rows": [{"destination": "peer.example/x", "fallback": "deny"}]}', "a destination has no /"),
    ('{"rows": [{"destination": "*.peer.example", "fallback": "deny"}]}', "a destination has no wildcard"),
    ('{"rows": [{"destination": "peer?.example", "fallback": "deny"}]}', "a destination has no wildcard"),
    ('{"rows": [{"destination": "peer[1].example", "fallback": "deny"}]}', "a destination has no wildcard"),
    ('{"rows": [{"destination": "${HOME}.example", "fallback": "deny"}]}', "a destination has no $"),
    ('{"rows": [{"destination": "peer.example", "fallback": "deny"}, {"destination": "Peer.Example.", "wait_ms": 5}]}',
     "a duplicate destination"),
    ('{"rows": [{"destination": "peer.example", "fallback": "deny"}, {"destination": "PEER.example", "wait_ms": 5}]}',
     "a duplicate destination"),
    ('{"rows": [{"destination": "peer.example"}]}', "a row gives wait_ms or fallback"),
    ('{"rows": [{"destination": "peer.example", "wait_ms": 0}]}', "wait_ms is an integer 1..3000"),
    ('{"rows": [{"destination": "peer.example", "wait_ms": 3001}]}', "wait_ms is an integer 1..3000"),
    ('{"rows": [{"destination": "peer.example", "wait_ms": "100"}]}', "wait_ms is an integer 1..3000"),
    ('{"rows": [{"destination": "peer.example", "wait_ms": true}]}', "wait_ms is an integer 1..3000"),
    ('{"rows": [{"destination": "peer.example", "wait_ms": 100.0}]}', "wait_ms is an integer 1..3000"),
    ('{"rows": [{"destination": "peer.example", "fallback": "open"}]}', "fallback is deny or allow"),
    ('{"rows": [{"destination": "peer.example", "fallback": "Allow"}]}', "fallback is deny or allow"),
])
def test_each_rule_refuses_with_its_name(tmp_path, text, rule) -> None:
    """D2: a document breaking one rule of the shape exits 1, the rule named; the file and render are refused alike."""
    doc = tmp_path / "doc.json"
    doc.write_text(text)
    result = run("validate", str(doc))
    assert result.returncode == 1 and rule in result.stderr, result.stderr
    assert doc.read_text() == text
    rendered = run("render", stdin=text)
    assert rendered.returncode == 1 and rule in rendered.stderr and rendered.stdout == ""


def test_every_problem_is_its_own_line(tmp_path) -> None:
    """D2: a document breaking several rules gets one line per problem, each naming where."""
    doc = tmp_path / "doc.json"
    doc.write_text('{"rows": [{"destination": "a:1"}, {"destination": "b", "fallback": "open"}]}')
    lines = run("validate", str(doc)).stderr.splitlines()
    assert len(lines) == 3, lines
    assert "rows[0]: a destination has no port" in lines[0] and "rows[0]: a row gives wait_ms or fallback" in lines[1]
    assert "rows[1]: fallback is deny or allow" in lines[2]


def test_a_missing_file_is_refused(tmp_path) -> None:
    """D2: validate on a file that cannot be read exits 1."""
    result = run("validate", str(tmp_path / "nope.json"))
    assert result.returncode == 1 and "cannot be read" in result.stderr
