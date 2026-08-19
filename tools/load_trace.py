"""Dev/ops tool: replay a captured trace through the OTLP receiver.

WHY THIS EXISTS
---------------
A trace is stored as a set of ``spans``-table rows (ADR-0006, "the row is the
Span"); graph-interaction fixtures (``tests/processors/interactions/graph/fixtures/*.json``)
are verbatim snapshots of such rows. The sibling ``load_fixture.py`` loads them
by writing rows *directly* into Postgres via ``write_span`` — which bypasses the
OTLP receiver entirely.

This tool instead **replays** a trace over the wire, so the full production
ingest path runs end to end: OTLP transport → ``translate.request_to_span_rows``
→ ``write_span`` → finalization → NOTIFY → downstream processors. It talks only
OTLP; it never touches ``DATABASE_URL``.

WHAT IT DOES
------------
It **inverts** ``data_governance.processors.otlp_receiver.translate``: each span
row (a ``SpanRow``-shaped dict) is rebuilt into an OTLP protobuf span, grouped back
into the ``ResourceSpans -> ScopeSpans -> Span`` envelope, and shipped as a single
``ExportTraceServiceRequest``. It deliberately does NOT go through the OpenTelemetry
SDK exporter — that path drops or mangles exactly the fields these traces exercise
(span ``kind``, ``Status`` UNSET, ``events``, ``links``, ``scope`` shape, and the
``otlp`` envelope). Building the protobuf by hand keeps the replay faithful.

The enum and value mappings are the single source of truth in ``translate``; this
tool imports and inverts them rather than re-declaring the pairs.

HOW TO RUN
----------
    # dry run: build the request, print counts, send nothing
    python tools/load_trace.py travel_agent_II --dry-run

    # replay via gRPC (default) to a local receiver on :4317
    python tools/load_trace.py travel_agent_II

    # replay via HTTP/protobuf to :4318, custom endpoint
    python tools/load_trace.py travel_agent_II \
        --transport http --endpoint http://localhost:4318

    # shift all times so the trace ends "now" — reads as just-arrived in the UI
    python tools/load_trace.py travel_agent_II --now

    # fresh trace_id + span_ids, shows in last-hour view
    python tools/load_trace.py travel_agent_II --reid --now

Use ``--reid`` when re-loading a fixture you have already ingested: the fixture's
deterministic ``trace_id`` makes a plain re-replay a no-op (``write_span`` upserts
are finalization-only, never rewriting the identity/times, and the interactions
cursor has already passed those seqs). ``--reid`` rewrites the trace to fresh
random ids so it reads as genuinely new, cursor-visible data — compose it with
``--now`` to also land it in the last-hour view.

The trace argument accepts a full path, a path ending in ``.json``, or a bare
stem (e.g. ``travel_agent_II``) resolved under the graph fixtures directory.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import secrets
import sys
from pathlib import Path
from typing import Any

from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
from opentelemetry.proto.common.v1 import common_pb2
from opentelemetry.proto.resource.v1 import resource_pb2
from opentelemetry.proto.trace.v1 import trace_pb2

# Single source of truth: invert the receiver's forward maps rather than
# re-typing the pairs. If translate.py grows a span kind, this tool follows.
from data_governance.processors.otlp_receiver import translate

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURES = (
    _REPO_ROOT
    / "tests"
    / "processors"
    / "interactions"
    / "graph"
    / "fixtures"
)
_SERVICE_NAME_KEY = translate._SERVICE_NAME_KEY

# name -> OTLP enum, inverse of translate._SPAN_KIND_NAMES. A None/unknown
# kind falls back to SPAN_KIND_UNSPECIFIED (the value translate maps to None).
_SPAN_KIND_VALUES: dict[str, int] = {
    name: value for value, name in translate._SPAN_KIND_NAMES.items()
}


# ---------------------------------------------------------------------------
# Value + timestamp primitives (inverse of translate)
# ---------------------------------------------------------------------------


def _dt_to_ns(value: str | None) -> int:
    """ISO-8601 timestamp string -> Unix nanoseconds; ``None``/empty -> 0.

    Inverse of ``translate._ns_to_dt`` (which truncated ns->us on ingest, so a
    replay is microsecond-faithful, not nanosecond-faithful — the sub-us digits
    were already gone when the fixture was captured). ``ended_at is None`` maps
    to 0, matching the receiver's "unended span" sentinel.
    """
    if not value:
        return 0
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    # Integer arithmetic on purpose: ``timestamp() * 1e9`` accumulates float
    # error that shifts whole microseconds (e.g. ...799804us -> ...799803999ns,
    # which translate then truncates back to 799803us). Fixtures are already
    # microsecond-resolution, so scale the epoch-microseconds up by 1000 exactly.
    epoch = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    micros = (parsed - epoch) // dt.timedelta(microseconds=1)
    return micros * 1000


def _parse_dt(value: str) -> dt.datetime:
    """ISO-8601 string -> aware UTC datetime (naive is assumed UTC)."""
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def shift_rows_to_now(
    rows: list[dict[str, Any]], now: dt.datetime
) -> tuple[list[dict[str, Any]], dt.timedelta]:
    """Return copies of ``rows`` with all timestamps shifted so the latest is ``now``.

    One offset (``now - max_timestamp``) is applied to every time field
    (``started_at``, ``ended_at``, event ``time_unix_nano``), preserving durations
    and ordering. Rows without timestamps are returned unchanged.
    """
    # Find the latest instant across starts and ends (ns events are derived from
    # the same wall clock, but starts/ends bound the trace, so they set the anchor).
    latest: dt.datetime | None = None
    for row in rows:
        for key in ("started_at", "ended_at"):
            val = row.get(key)
            if val:
                ts = _parse_dt(val)
                if latest is None or ts > latest:
                    latest = ts
    if latest is None:
        return rows, dt.timedelta(0)

    offset = now - latest
    offset_ns = offset // dt.timedelta(microseconds=1) * 1000

    shifted: list[dict[str, Any]] = []
    for row in rows:
        new_row = dict(row)
        for key in ("started_at", "ended_at"):
            val = row.get(key)
            if val:
                new_row[key] = (_parse_dt(val) + offset).isoformat()
        events = row.get("events")
        if events:
            new_events = []
            for ev in events:
                new_ev = dict(ev)
                t = ev.get("time_unix_nano")
                if t:
                    new_ev["time_unix_nano"] = int(t) + offset_ns
                new_events.append(new_ev)
            new_row["events"] = new_events
        shifted.append(new_row)
    return shifted, offset


def _remap_id_refs(value: Any, ref_map: dict[str, str]) -> Any:
    """Deep-copy *value*, replacing every string that is exactly a key of
    *ref_map* with its mapped id. Non-matching strings and all other scalars are
    returned unchanged. Used by :func:`_reid_rows` to carry id references inside
    attributes / events / links onto the new ids.
    """
    if isinstance(value, str):
        return ref_map.get(value, value)
    if isinstance(value, dict):
        return {k: _remap_id_refs(v, ref_map) for k, v in value.items()}
    if isinstance(value, list):
        return [_remap_id_refs(v, ref_map) for v in value]
    return value


def _reid_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    """Return copies of ``rows`` rewritten onto a fresh random trace + span ids.

    WHY: fixtures carry a deterministic ``trace_id``, so a plain re-replay of one
    you have already ingested is a no-op — ``write_span`` upserts are finalization-
    only (they never rewrite the identity or ``started_at``) and the interactions
    cursor has already passed those seqs. Rewriting to a brand-new ``trace_id`` and
    new ``span_id``s yields genuinely fresh, cursor-visible data that ``--now`` can
    shift into the last-hour view.

    One new 32-hex ``trace_id`` is minted; every distinct ``span_id`` is mapped to a
    new 16-hex id. ``parent_id`` is remapped through the *same* map when present so
    the parent/child tree (and thus the derived interactions) is preserved; a
    missing/empty/root parent is left as-is. The ids only need to be unique, not
    reproducible, so ``secrets`` supplies the randomness.

    REFERENCES TO IDS ARE REMAPPED TOO. ``span_id`` and ``parent_id`` are not the
    only places a span id appears: instrumentation also records ids *inside*
    ``attributes``, ``events`` and ``links`` — a link's ``span_id``/``trace_id``
    structurally, and attributes that point at another span by id. Rewriting the
    identity while leaving those references pointing at the old ids produces a
    trace that is internally inconsistent, and a consumer that reads such an
    attribute and checks it against the span's own id sees a contradiction that
    cannot occur in real data. So every string in those three fields that is
    *exactly* an old span id or the old trace id is remapped through the same
    map, at any nesting depth.

    The match is deliberately whole-string, never substring: ids also occur inside
    captured payload bodies (a serialized ``traceparent``, say), and those are
    opaque content — rewriting them would corrupt a body the store may have
    content-addressed. A body that mentions the old trace is harmless; a
    structural reference to it is not.
    """
    new_trace_id = secrets.token_hex(16)  # 32 lowercase hex chars
    id_map = {
        span_id: secrets.token_hex(8)  # 16 lowercase hex chars
        for span_id in {r["span_id"] for r in rows}
    }
    # Ids referenced from within a span's fields: span ids, plus every old trace
    # id present (normally one; a mixed-trace capture is still handled).
    ref_map = dict(id_map)
    ref_map.update({r["trace_id"]: new_trace_id for r in rows})

    reidd: list[dict[str, Any]] = []
    for row in rows:
        new_row = dict(row)
        new_row["trace_id"] = new_trace_id
        new_row["span_id"] = id_map[row["span_id"]]
        parent = row.get("parent_id")
        if parent:  # non-empty / non-None: remap; root stays as-is
            new_row["parent_id"] = id_map.get(parent, parent)
        for field in ("attributes", "events", "links"):
            if row.get(field):
                new_row[field] = _remap_id_refs(row[field], ref_map)
        reidd.append(new_row)
    return reidd, new_trace_id


def _py_to_any_value(value: Any) -> common_pb2.AnyValue:
    """Python value -> OTLP ``AnyValue``; inverse of ``translate._any_value_to_python``.

    Note the same lossiness ``translate._kvs_to_dict`` documents in reverse: JSON
    has only one number type, so a Python ``int`` becomes an OTLP ``int_value`` and
    a ``float`` becomes a ``double_value`` — the original wire type is a guess. A
    fixture attribute that was a double whole-number (e.g. ``1.0``) would have been
    captured as ``1.0`` in JSON and round-trips as a double; one captured as ``1``
    round-trips as an int. This matches how the value entered the fixture.
    """
    av = common_pb2.AnyValue()
    if value is None:
        return av  # no oneof set — WhichOneof(...) is None, as translate expects
    if isinstance(value, bool):  # bool before int: bool is a subclass of int
        av.bool_value = value
    elif isinstance(value, int):
        av.int_value = value
    elif isinstance(value, float):
        av.double_value = value
    elif isinstance(value, str):
        av.string_value = value
    elif isinstance(value, list):
        av.array_value.values.extend(_py_to_any_value(v) for v in value)
    elif isinstance(value, dict):
        av.kvlist_value.values.extend(_dict_to_kvs(value))
    else:
        # Unknown type: stringify. Fixtures are JSON so this shouldn't fire.
        av.string_value = str(value)
    return av


def _dict_to_kvs(mapping: dict[str, Any]) -> list[common_pb2.KeyValue]:
    """dict -> OTLP ``repeated KeyValue``; inverse of ``translate._kvs_to_dict``."""
    return [
        common_pb2.KeyValue(key=k, value=_py_to_any_value(v))
        for k, v in mapping.items()
    ]


# ---------------------------------------------------------------------------
# Row -> protobuf span (inverse of translate._span_to_row)
# ---------------------------------------------------------------------------


def _error_to_status(row: dict[str, Any]) -> trace_pb2.Status:
    """Rebuild OTLP ``Status`` from the tri-state ``error`` + ``status_message``.

    Inverse of ``translate._status_code_to_error``: True->ERROR, False->OK,
    None->UNSET.
    """
    error = row.get("error")
    if error is True:
        code = trace_pb2.Status.STATUS_CODE_ERROR
    elif error is False:
        code = trace_pb2.Status.STATUS_CODE_OK
    else:
        code = trace_pb2.Status.STATUS_CODE_UNSET
    return trace_pb2.Status(code=code, message=row.get("status_message") or "")


def _events_from_row(row: dict[str, Any]) -> list[trace_pb2.Span.Event]:
    """Inverse of ``translate._events_to_list``."""
    out: list[trace_pb2.Span.Event] = []
    for ev in row.get("events") or []:
        out.append(
            trace_pb2.Span.Event(
                name=ev.get("name", ""),
                time_unix_nano=int(ev.get("time_unix_nano") or 0),
                attributes=_dict_to_kvs(ev.get("attributes") or {}),
            )
        )
    return out


def _links_from_row(row: dict[str, Any]) -> list[trace_pb2.Span.Link]:
    """Inverse of ``translate._links_to_list``."""
    out: list[trace_pb2.Span.Link] = []
    for ln in row.get("links") or []:
        out.append(
            trace_pb2.Span.Link(
                trace_id=bytes.fromhex(ln["trace_id"]),
                span_id=bytes.fromhex(ln["span_id"]),
                attributes=_dict_to_kvs(ln.get("attributes") or {}),
            )
        )
    return out


def _row_to_span(row: dict[str, Any]) -> trace_pb2.Span:
    """Rebuild one OTLP ``Span`` from a fixture row; inverse of ``_span_to_row``."""
    otlp = row.get("otlp") or {}
    parent_hex = row.get("parent_id")
    span = trace_pb2.Span(
        trace_id=bytes.fromhex(row["trace_id"]),
        span_id=bytes.fromhex(row["span_id"]),
        parent_span_id=bytes.fromhex(parent_hex) if parent_hex else b"",
        name=row.get("name", ""),
        kind=_SPAN_KIND_VALUES.get(
            row.get("kind"), trace_pb2.Span.SPAN_KIND_UNSPECIFIED
        ),
        start_time_unix_nano=_dt_to_ns(row.get("started_at")),
        end_time_unix_nano=_dt_to_ns(row.get("ended_at")),
        status=_error_to_status(row),
        attributes=_dict_to_kvs(row.get("attributes") or {}),
        events=_events_from_row(row),
        links=_links_from_row(row),
        # otlp envelope (translate._otlp_envelope), all optional / non-default:
        trace_state=otlp.get("trace_state", ""),
        flags=int(otlp.get("flags") or 0),
        dropped_attributes_count=int(otlp.get("dropped_attributes_count") or 0),
        dropped_events_count=int(otlp.get("dropped_events_count") or 0),
        dropped_links_count=int(otlp.get("dropped_links_count") or 0),
    )
    return span


# ---------------------------------------------------------------------------
# Rows -> ExportTraceServiceRequest (inverse of request_to_span_rows)
# ---------------------------------------------------------------------------


def _resource_key(row: dict[str, Any]) -> tuple:
    """Hashable grouping key for a row's resource (service_name + resource attrs)."""
    res_attrs = row.get("resource_attributes") or {}
    return (
        row.get("service_name"),
        tuple(sorted((k, repr(v)) for k, v in res_attrs.items())),
    )


def _scope_key(row: dict[str, Any]) -> tuple:
    """Hashable grouping key for a row's instrumentation scope."""
    scope = row.get("scope") or {}
    return (
        scope.get("name"),
        scope.get("version"),
        tuple(sorted((k, repr(v)) for k, v in (scope.get("attributes") or {}).items())),
    )


def _build_resource(row: dict[str, Any]) -> resource_pb2.Resource:
    """Rebuild ``Resource``: resource_attributes + re-promoted service.name.

    Inverse of ``translate._resource_service_name`` +
    ``_resource_attributes_minus_service_name``.
    """
    attrs = dict(row.get("resource_attributes") or {})
    service_name = row.get("service_name")
    if service_name is not None:
        attrs[_SERVICE_NAME_KEY] = service_name
    return resource_pb2.Resource(attributes=_dict_to_kvs(attrs))


def _build_scope(row: dict[str, Any]) -> common_pb2.InstrumentationScope:
    """Rebuild ``InstrumentationScope``; inverse of ``_instrumentation_scope_to_dict``."""
    scope = row.get("scope") or {}
    return common_pb2.InstrumentationScope(
        name=scope.get("name", ""),
        version=scope.get("version", ""),
        attributes=_dict_to_kvs(scope.get("attributes") or {}),
    )


def rows_to_request(
    rows: list[dict[str, Any]],
) -> trace_service_pb2.ExportTraceServiceRequest:
    """Group fixture rows back into one OTLP ``ExportTraceServiceRequest``.

    Rows are bucketed by resource then scope, reconstructing the
    ``ResourceSpans -> ScopeSpans -> Span`` nesting that
    ``translate.request_to_span_rows`` flattened on ingest.
    """
    request = trace_service_pb2.ExportTraceServiceRequest()

    # resource_key -> (ResourceSpans, {scope_key -> ScopeSpans})
    resources: dict[tuple, tuple[trace_pb2.ResourceSpans, dict[tuple, Any]]] = {}

    for row in rows:
        rkey = _resource_key(row)
        if rkey not in resources:
            rs = request.resource_spans.add()
            rs.resource.CopyFrom(_build_resource(row))
            resources[rkey] = (rs, {})
        rs, scopes = resources[rkey]

        skey = _scope_key(row)
        if skey not in scopes:
            ss = rs.scope_spans.add()
            ss.scope.CopyFrom(_build_scope(row))
            scopes[skey] = ss
        ss = scopes[skey]

        ss.spans.append(_row_to_span(row))

    return request


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------


def _send_grpc(
    request: trace_service_pb2.ExportTraceServiceRequest, endpoint: str
) -> trace_service_pb2.ExportTraceServiceResponse:
    """Send via OTLP gRPC (mirrors tests/harness/fixtures.py::send_grpc_request)."""
    import grpc
    from opentelemetry.proto.collector.trace.v1 import trace_service_pb2_grpc

    with grpc.insecure_channel(endpoint) as channel:
        stub = trace_service_pb2_grpc.TraceServiceStub(channel)
        try:
            return stub.Export(request)
        except grpc.RpcError as exc:
            # Mirror _send_http's clean exit. UNAVAILABLE => receiver's DB is
            # down and the export is retryable (ADR-0003); other codes are the
            # gRPC status of the failure. Either way, no spans persisted.
            code = exc.code()  # type: ignore[attr-defined]
            detail = exc.details()  # type: ignore[attr-defined]
            raise SystemExit(
                f"receiver returned gRPC {code.name}: {detail} — "
                f"retryable, no spans persisted"
            ) from exc


def _send_http(
    request: trace_service_pb2.ExportTraceServiceRequest, endpoint: str
) -> trace_service_pb2.ExportTraceServiceResponse:
    """Send via OTLP HTTP/protobuf (mirrors the receiver's _traces_handler)."""
    import urllib.error
    import urllib.request

    url = endpoint.rstrip("/") + "/v1/traces"
    req = urllib.request.Request(
        url,
        data=request.SerializeToString(),
        headers={"Content-Type": "application/x-protobuf"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        # 503 => retryable DB error on the receiver side (ADR-0003).
        raise SystemExit(
            f"receiver returned HTTP {exc.code}: {exc.reason} — "
            f"retryable, no spans persisted"
        ) from exc
    out = trace_service_pb2.ExportTraceServiceResponse()
    out.ParseFromString(body)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_fixture(arg: str) -> Path:
    """Accept a full path, a path ending in .json, or a bare fixture stem.

    Same resolution shape as ``load_fixture._resolve_fixture``.
    """
    p = Path(arg)
    if p.exists():
        return p
    return _FIXTURES / (arg if arg.endswith(".json") else f"{arg}.json")


def _default_endpoint(transport: str) -> str:
    return "localhost:4317" if transport == "grpc" else "http://localhost:4318"


def _report_response(
    resp: trace_service_pb2.ExportTraceServiceResponse, sent: int
) -> int:
    """Print the OTLP outcome; return process exit code."""
    ps = resp.partial_success
    if ps.rejected_spans or ps.error_message:
        print(
            f"  partial success: {ps.rejected_spans} of {sent} span(s) rejected"
            + (f" — {ps.error_message}" if ps.error_message else "")
        )
        return 1
    print(f"  ok: {sent} span(s) accepted")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="load_trace",
        description="Replay a span fixture through the OTLP receiver.",
    )
    parser.add_argument(
        "fixture",
        help="fixture path, *.json path, or bare stem (e.g. travel_agent_II)",
    )
    parser.add_argument(
        "--transport",
        choices=("grpc", "http"),
        default="grpc",
        help="OTLP transport (default: grpc)",
    )
    parser.add_argument(
        "--endpoint",
        default=None,
        help="receiver endpoint (default: localhost:4317 grpc / "
        "http://localhost:4318 http)",
    )
    parser.add_argument(
        "--reid",
        action="store_true",
        help="rewrite the trace onto a fresh random trace_id + span_ids before "
        "sending, so re-loading a fixture you have already ingested reads as "
        "genuinely new data (a plain re-replay is a no-op); compose with --now "
        "to land it in the last-hour view",
    )
    parser.add_argument(
        "--now",
        action="store_true",
        help="shift all span times by a single offset so the latest lands on "
        "now — the trace reads as just-arrived in the UI while keeping its "
        "internal durations and ordering",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build the request and print counts; send nothing",
    )
    args = parser.parse_args(argv)

    fixture = _resolve_fixture(args.fixture)
    if not fixture.exists():
        print(f"ERROR: fixture not found: {fixture}", file=sys.stderr)
        return 1

    rows = json.loads(fixture.read_text())
    if not rows:
        print(f"ERROR: fixture {fixture} has no span rows", file=sys.stderr)
        return 1

    if args.reid:
        rows, new_trace_id = _reid_rows(rows)
        print(f"  reid: new trace_id {new_trace_id}")

    if args.now:
        rows, offset = shift_rows_to_now(rows, dt.datetime.now(dt.timezone.utc))
        print(f"  shifted times by {offset} so the trace ends at now")

    trace_ids = {r["trace_id"] for r in rows}
    request = rows_to_request(rows)
    n_resources = len(request.resource_spans)
    n_scopes = sum(len(rs.scope_spans) for rs in request.resource_spans)

    print(f"fixture {fixture.name}: {len(rows)} span(s), {n_resources} resource(s), "
          f"{n_scopes} scope(s), {len(trace_ids)} trace(s)")
    if len(trace_ids) == 1:
        print(f"  trace_id: {next(iter(trace_ids))}")

    if args.dry_run:
        print("  dry run — nothing sent")
        return 0

    endpoint = args.endpoint or _default_endpoint(args.transport)
    print(f"sending via {args.transport} to {endpoint} ...")
    if args.transport == "grpc":
        resp = _send_grpc(request, endpoint)
    else:
        resp = _send_http(request, endpoint)

    return _report_response(resp, len(rows))


if __name__ == "__main__":
    raise SystemExit(main())
