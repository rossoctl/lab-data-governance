"""deploy/fail_mode.py: the fail-mode document's validator and the rows and slices it renders for the Sentry gate (#342)."""

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
    """D2: a good document exits 0; render gives each row with only its keys, in destination, wait_ms, fallback order;
    with no row naming a source there is no sources key."""
    doc = tmp_path / "doc.json"
    doc.write_text(json.dumps(GOOD))
    before = doc.read_bytes()
    result = run("validate", str(doc))
    assert result.returncode == 0 and result.stderr == "", result.stderr
    assert "3 rows" in result.stdout
    assert doc.read_bytes() == before
    rendered = run("render", stdin=json.dumps(GOOD))
    assert rendered.returncode == 0, rendered.stderr
    assert rendered.stdout == json.dumps({"fail_mode": [{"destination": "peer.example", "fallback": "allow"},
                                                       {"destination": "slow.example", "wait_ms": 3000},
                                                       {"destination": "Edge.Example", "wait_ms": 1, "fallback": "deny"}]})
    assert run("render", stdin='{"rows": []}').stdout == '{"fail_mode": []}'


@pytest.mark.parametrize(("text", "rule"), [
    ("{", "not JSON"),
    ('{"rows": [], "version": 1}', 'the document is {"rows": [...]}, one key'),
    ('[{"destination": "peer.example", "fallback": "deny"}]', 'the document is {"rows": [...]}, one key'),
    ('{"rows": {"destination": "peer.example"}}', "rows is a list"),
    ('{"rows": ["peer.example"]}', "a row is an object"),
    ('{"rows": [{"destination": "peer.example", "fallback": "deny", "host": "a"}]}', "unknown key 'host'"),
    ('{"rows": [{"fallback": "deny"}]}', "a row names a source, a destination or both"),
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
    # D9: the source and the (source, destination) key.
    ('{"rows": [{"source": "", "fallback": "deny"}]}', "an empty source"),
    ('{"rows": [{"source": 7, "fallback": "deny"}]}', "an empty source"),
    ('{"rows": [{"source": "travel advisor", "fallback": "deny"}]}', "a source has no whitespace"),
    ('{"rows": [{"source": "desk\\t", "fallback": "deny"}]}', "a source has no whitespace"),
    ('{"rows": [{"source": "ns/desk", "fallback": "deny"}]}', "a source has no /"),
    ('{"rows": [{"source": "$DESK", "fallback": "deny"}]}', "a source has no $"),
    ('{"rows": [{"source": "desk"}]}', "a row gives wait_ms or fallback"),
    ('{"rows": [{"source": "desk", "fallback": "deny"}, {"source": "desk", "wait_ms": 5}]}', "a duplicate source"),
    ('{"rows": [{"source": "desk", "destination": "peer.example", "fallback": "deny"},'
     ' {"source": "desk", "destination": "PEER.example", "wait_ms": 5}]}', "a duplicate pair"),
    ('{"rows": [{"source": "desk", "destination": "peer.example", "fallback": "deny"},'
     ' {"source": "desk", "destination": "peer.example.", "wait_ms": 5}]}', "a duplicate pair"),
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


def test_a_source_row_a_destination_row_and_a_pair_on_one_edge_are_three_rows(tmp_path) -> None:
    """D9: the duplicate key is (source, destination): a source row, a destination row and a pair row naming the same
    names are not duplicates, nor are two sources' pairs to one destination or two sources differing in case."""
    doc = tmp_path / "doc.json"
    doc.write_text(json.dumps({"rows": [{"source": "desk", "fallback": "deny"}, {"destination": "peer.example", "wait_ms": 5},
                                        {"source": "desk", "destination": "peer.example", "wait_ms": 6},
                                        {"source": "Desk", "destination": "peer.example", "wait_ms": 7}]}))
    result = run("validate", str(doc))
    assert result.returncode == 0, result.stderr
    assert "4 rows, 2 sources" in result.stdout


# D10: a source row, a destination row, a pair row of that source and destination, and a second destination.
ONE_OF_EACH = {"rows": [{"source": "desk", "wait_ms": 100},
                        {"destination": "peer.example", "wait_ms": 50, "fallback": "deny"},
                        {"fallback": "allow", "destination": "peer.example", "source": "desk"},
                        {"destination": "slow.example", "wait_ms": 3000}]}


def test_render_fills_a_slice_pair_over_destination_with_the_source_as_defaults() -> None:
    """D10: fail_mode = the destination rows; the slice's defaults = the source row's keys; its rows = for each
    destination, the pair's keys over the destination row's (peer.example: wait 50 from the destination, allow from
    the pair), the second destination carried as is. Only keys given: the gate fills the rest."""
    rendered = run("render", stdin=json.dumps(ONE_OF_EACH))
    assert rendered.returncode == 0, rendered.stderr
    assert rendered.stdout == (
        '{"fail_mode": [{"destination": "peer.example", "wait_ms": 50, "fallback": "deny"}, '
        '{"destination": "slow.example", "wait_ms": 3000}], '
        '"sources": {"desk": {"wait_ms": 100, "fail_mode": [{"destination": "peer.example", "wait_ms": 50, "fallback": "allow"}, '
        '{"destination": "slow.example", "wait_ms": 3000}]}}}')


def test_a_slice_holds_only_what_its_rows_give() -> None:
    """D10: a source with only pair rows has no defaults; a source with only a source row and no destination rows has no
    rows; slices and their rows keep document order, a destination first named by a pair included."""
    doc = {"rows": [{"source": "b", "destination": "x.example", "wait_ms": 7},
                    {"source": "a", "fallback": "deny"},
                    {"source": "b", "destination": "y.example", "fallback": "deny"}]}
    rendered = run("render", stdin=json.dumps(doc))
    assert rendered.returncode == 0, rendered.stderr
    assert json.loads(rendered.stdout) == {"fail_mode": [], "sources": {
        "b": {"fail_mode": [{"destination": "x.example", "wait_ms": 7}, {"destination": "y.example", "fallback": "deny"}]},
        "a": {"fallback": "deny"}}}
    assert list(json.loads(rendered.stdout)["sources"]) == ["b", "a"]


def test_a_missing_file_is_refused(tmp_path) -> None:
    """D2: validate on a file that cannot be read exits 1."""
    result = run("validate", str(tmp_path / "nope.json"))
    assert result.returncode == 1 and "cannot be read" in result.stderr


# D15: the revision is the sha256 of the document's bytes, as set keeps them.
CRLF = b'{"rows": [ {"destination": "Peer.example",\r\n "wait_ms": 100} ]}  \r\n\r\n'


def test_the_revision_is_the_sha256_of_the_bytes(tmp_path) -> None:
    """D15: revision <file> prints hashlib's sha256 of the file's bytes, \\r\\n and trailing whitespace included (no
    normalising); - reads the same bytes from stdin; a one-byte change gives another revision."""
    import hashlib

    doc = tmp_path / "doc.json"
    doc.write_bytes(CRLF)
    result = run("revision", str(doc))
    assert result.returncode == 0 and result.stderr == "", result.stderr
    assert result.stdout == hashlib.sha256(CRLF).hexdigest() + "\n"
    assert result.stdout != hashlib.sha256(CRLF.replace(b"\r\n", b"\n")).hexdigest() + "\n"
    piped = subprocess.run([sys.executable, str(SCRIPT), "revision", "-"], input=CRLF, capture_output=True, timeout=30)
    assert piped.returncode == 0 and piped.stdout.decode() == result.stdout
    doc.write_bytes(CRLF.replace(b"100", b"101"))
    changed = run("revision", str(doc))
    assert changed.returncode == 0 and changed.stdout != result.stdout and len(changed.stdout.strip()) == 64


def test_the_revision_of_a_missing_file_is_refused(tmp_path) -> None:
    """D15: revision on a file that cannot be read exits 1."""
    result = run("revision", str(tmp_path / "nope.json"))
    assert result.returncode == 1 and "cannot be read" in result.stderr and result.stdout == ""
