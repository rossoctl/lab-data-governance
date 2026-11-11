"""`GET /enforce/v1/verdicts/{trace_id}/{exchange_id}` — the verdict a Sentry gate
waits on.

A sidecar's gate holds an outbound request and asks for the governance verdict on
that one exchange: the trace id and the request span id (``lineage.exchange.id``,
which the sidecar stamps into the forwarded tracestate). Both are already the key
of everything derived from the exchange — the interaction id is
``uuid5(trace_id/exchange_id)`` (:func:`procedure._interaction_id`, the
derivation's own formula) — so the verdict is simply the exchange's **Interaction
risk record**: the record the console shows is the record the gate acts on.
Leg-ready stays its only writer; this module only reads it and asks for it.

The request is a read with no body; the key is the path, so it is a plain GET,
safe and idempotent, and a gate may repeat it. ``wait_ms`` (0..:data:`MAX_WAIT_MS`,
larger is refused, never clamped) turns it into a long poll: the answer comes as
soon as the exchange's record is decided, or at ``wait_ms`` with ``state: pending``;
the first read counts against ``wait_ms`` too, so a database still not answering at
``wait_ms`` gives ``pending`` as well. ``wait_ms=0`` is one read.

**Decided.** The latest record that evidences the request leg — the gate is
holding the request, so that record is the complete answer to "may this request
leave". When the interaction has no request leg at all, its latest record: the
sidecar algorithm always writes the request leg, so this arm only answers a
derivation that never writes one (the graph algorithm's request-less call), and a
gate never waits out its budget on a record that can never evidence it.

**Unjudged.** A record judged only what it was given. The exchange's request span
says what that was (wire contract §5): ``http.request.body.size`` is always there,
and a size above 0 beside no ``input.value`` is a body the sidecar saw and did not
emit (``capture_io`` off, not text, not whole, a parser that reduced it to nothing);
a value cut at ``max_payload_bytes`` ends ``…[truncated]``. So a record whose request
body was not read whole (:func:`~data_governance.retrieval.get_exchange_requests` derives
it) answers ``state: unjudged``, with the record's verdict and a
``reason`` (``body_not_captured``, ``body_truncated``). A ``block`` on what was read
still blocks; what to do with anything else is the gate's choice.
No value and no size (a sidecar older than contract v1.8) counts as not captured.

This reads what the sidecar reports, no further. For a2a, mcp and inference the value
is the parser's reduction (contract §5): what a parser leaves out of it no record
judges, and no span fact says it was left out, so such an exchange answers ``decided``.

**What was caught.** An answer with a record carries ``rules``, the record's
``triggered_rule_ids``, and ``labels``, the sorted distinct categories of the request
payload's own findings (a finding's entity type when its category is empty). A
``block`` also carries ``redacted_payload``: the classifier's own projection of the
payload (:func:`~data_governance.processors.classification.projection.project`, whose
text the findings' offsets index), each finding replaced by ``[<label>]``; overlapping
findings merge into one placeholder naming the first. So for a tool call it is the
canonical JSON of the arguments, not the agent's bytes, and for a body cut at
``max_payload_bytes`` it is the cut text. It replaces findings and nothing else: what
the classifier did not flag stays. It is all or nothing, and never fails the answer: a
payload above :data:`REDACTED_MAX_BYTES` leaves the field out, and so does a finding that
does not fit the text at its offsets, with a log line naming offsets only. These are read for
the answered ids only, so a wake fetches no body for a waiter still
waiting. The answer names the exchange by ``exchange_id``, the sidecar's own word for it.

**Who may ask.** ``Authorization: Bearer <VERDICT_SECRET>`` (constant-time compare;
401 otherwise); the process refuses to start without a secret
(:mod:`.verdict_app`). The secret identifies a gated sidecar, not a workload.

**Cap.** At most ``VERDICT_MAX_WAITERS`` requests are in flight per process; beyond
it the answer is 503 ``verdict_busy`` at once, never a queue.

**One pass per wake.** Each process keeps a waiter map keyed by interaction id and
one dispatcher subscribed to the change hub (``dg_legs_inserted``,
``dg_interaction_risk_written``; the notifies carry no payload). On every wake, and
at least every :data:`BACKSTOP_SECONDS` while anyone waits, the dispatcher runs one
batched pass for every waited interaction, whatever their number: the records from
:func:`~data_governance.retrieval.get_request_risk`, the requests from
:func:`~data_governance.retrieval.get_exchange_requests`, and what was caught from
:func:`~data_governance.retrieval.get_request_findings` for the answered ones, each read
its own transaction. It resolves only the answered ones. A new waiter registers first and then reads its own state once, so a
write landing between the two is seen by one of them. Every DB user is a bounded
executor (reads, priority classification), so no request waits on the pool: 64 waiters cost 64
first passes and then one pass per wake.

**Priority classification** (``VERDICT_PRIORITY_CLASSIFICATION=on``; this process then runs from the
classification image, with the model). When the waited exchange's request payload
exists and is not classified yet, it is classified here through the
classifier's own
:func:`~data_governance.processors.classification.driver.process_payload` — the
same write-once row (``ON CONFLICT (content_hash) DO NOTHING``), racing the cursor
loop harmlessly, no cursor touched — on a small worker pool
(``VERDICT_PRIORITY_CLASSIFICATION_CONCURRENCY``). The payload row may not exist yet (the request
span is in flight): the dispatcher sees it on the ``dg_legs_inserted`` wake that
lands it, inside the same ``wait_ms``. ``VERDICT_PRIORITY_DECISION=on`` also names the
interaction on ``dg_priority_decision`` (once when a wait starts undecided, once after
it is classified), and leg-ready's priority decision computes it out of ``seq``
order (:mod:`data_governance.processors.leg_ready.priority_decision`). ``VERDICT_PRIORITY_CLASSIFICATION=off``
does neither: the wait is the cursor path's alone.

One structured log line per request (``verdict {...}``): trace, exchange,
interaction id, status, state, verdict, waited_ms, whether this process
classified the request payload, and the classification ms.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hmac
import json
import logging
import os
import re
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Route

from data_governance import db, retrieval
from data_governance.processors.classification.projection import project
from data_governance.processors.interactions.procedure import _interaction_id

__all__ = ["PriorityClassifier", "Settings", "VerdictService", "routes", "verdict_handler"]

log = logging.getLogger(__name__)

#: Longest one request may wait; a larger wait_ms is refused.
MAX_WAIT_MS = 3000

#: Re-read at least this often while anyone waits, wake or not: the backstop for
#: a hub whose LISTEN session is down and for writes no channel announces.
BACKSTOP_SECONDS = 0.2

#: The change-hub channels that can change a waiter's state: a leg (and its
#: payload) landing, a risk record landing.
HUB_CHANNELS: tuple[str, ...] = ("dg_legs_inserted", "dg_interaction_risk_written")

#: The channel leg-ready's priority decision listens on (processors/leg_ready/priority_decision.py).
PRIORITY_DECISION_CHANNEL = "dg_priority_decision"

#: What the model classifies once before the process reports ready (drug and
#: dates, no identifier); nothing is written.
_WARM_SAMPLE = {
    "drug": "Lisinopril 10 mg tablets",
    "quantity": 30,
    "dispensed_on": "2026-01-05",
    "next_refill": "2026-02-04",
}
_WARM_HASH = "0" * 64

_TRACE_RE = re.compile(r"[0-9a-f]{32}")
_SPAN_RE = re.compile(r"[0-9a-f]{16}")
_WAIT_RE = re.compile(r"[0-9]{1,6}")

#: What a keyed log line keeps of a key it has not validated yet.
_LOG_KEY_CHARS = 64

#: Largest ``redacted_payload`` an answer carries, JSON-encoded, and the largest stored
#: payload fetched to make one. A gate reads 64 KiB of an answer; a block must never be
#: lost to the size of its text.
REDACTED_MAX_BYTES = 32 * 1024


def _switch(env: Mapping[str, str], name: str, default: str) -> bool:
    value = env.get(name, default).strip().lower()
    if value not in ("on", "off"):
        raise ValueError(f"{name} must be 'on' or 'off', got {value!r}")
    return value == "on"


def _int(env: Mapping[str, str], name: str, default: int, low: int) -> int:
    raw = env.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from None
    if value < low:
        raise ValueError(f"{name} must be >= {low}, got {value}")
    return value


@dataclasses.dataclass(frozen=True)
class Settings:
    """The service's knobs, read from the environment once at start."""

    secret: str = ""
    max_waiters: int = 64
    priority_classification: bool = True
    priority_decision: bool = True
    priority_classification_concurrency: int = 1
    read_workers: int = 4

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        classify = _switch(env, "VERDICT_PRIORITY_CLASSIFICATION", "on")
        return cls(
            secret=env.get("VERDICT_SECRET", "").strip(),
            max_waiters=_int(env, "VERDICT_MAX_WAITERS", 64, 1),
            priority_classification=classify,
            # Priority decision is part of priority classification: off means the cursor path alone.
            priority_decision=classify and _switch(env, "VERDICT_PRIORITY_DECISION", "on"),
            priority_classification_concurrency=_int(env, "VERDICT_PRIORITY_CLASSIFICATION_CONCURRENCY", 1, 1),
            read_workers=_int(env, "VERDICT_READ_WORKERS", 4, 1),
        )

    @property
    def pool_size(self) -> int:
        """Connections this process needs: one per read worker, one per classification
        worker (``process_payload`` holds its connection through inference),
        one spare. The hub's LISTEN session is outside the pool."""
        return self.read_workers + (self.priority_classification_concurrency if self.priority_classification else 0) + 1


#: A waited exchange: (interaction id, trace id, exchange id).
_Key = tuple[str, str, str]


@dataclasses.dataclass(frozen=True)
class _State:
    record: dict[str, Any] | None
    payload_hash: str | None
    payload_present: bool
    classified: bool


def _label(finding: Any) -> str | None:
    """A finding's name: its category, or its entity type when that is empty."""
    if isinstance(finding, dict):
        for key in ("category", "entity_type"):
            value = finding.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _labels(findings: Any) -> list[str]:
    """The sorted distinct names of *findings*; total over whatever the row holds."""
    if not isinstance(findings, list):
        return []
    return sorted({name for finding in findings if (name := _label(finding))})


class _Unrenderable(ValueError):
    """Why a block has no redacted payload; the message names offsets, never text."""


def _redact(content: Any, findings: Any) -> str:
    """The projection of *content* with each finding replaced by ``[<label>]``.
    Raises :class:`_Unrenderable` when a finding cannot be placed exactly."""
    if not isinstance(findings, list):
        raise _Unrenderable(f"findings are a {type(findings).__name__}, not a list")
    text = project(content)
    spans = []
    for i, finding in enumerate(findings):
        name = _label(finding)
        if name is None:
            raise _Unrenderable(f"finding {i} names no category or entity type")
        start, end = finding.get("start"), finding.get("end")
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
            raise _Unrenderable(f"finding {i}: span {start!r}..{end!r} is outside 0..{len(text)}")
        if finding.get("text") != text[start:end]:
            raise _Unrenderable(f"finding {i}: the text at {start}..{end} is not the finding's (drifted offset)")
        spans.append((start, end, name))
    merged: list[list] = []
    for start, end, name in sorted(spans, key=lambda s: (s[0], -s[1])):
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end, name])
    for start, end, name in reversed(merged):
        text = f"{text[:start]}[{name}]{text[end:]}"
    size = len(json.dumps(text, ensure_ascii=False).encode())
    if size > REDACTED_MAX_BYTES:
        raise _Unrenderable(f"the text is {size} bytes encoded, above {REDACTED_MAX_BYTES}")
    return text


def _redacted_payload(interaction_id: str, content: Any, findings: Any) -> str | None:
    """:func:`_redact`, or None and a log line. Never raises: this runs inside the
    batched read every waiter shares."""
    try:
        return _redact(content, findings)
    except _Unrenderable as exc:
        reason = str(exc)
    except Exception as exc:  # noqa: BLE001 — its message could quote the payload
        reason = f"rendering failed ({type(exc).__name__})"
    log.warning("verdict: no redacted payload for interaction %s: %s", interaction_id, reason)
    return None


def _read_states(keys: list[_Key], notify_undecided: bool = False) -> dict[str, _State]:
    """The state of every waited exchange, read through retrieval for all of them at
    once; with *notify_undecided*, name each undecided one on the priority decision
    channel.

    The record (:func:`~data_governance.retrieval.get_request_risk`) is read before the
    request (:func:`~data_governance.retrieval.get_exchange_requests`), so a leg landing
    between the two is not in the record's pick. For a held exchange that changes
    nothing: its request leg is derived before any record of it (a record is computed
    from legs, and the response leg cannot exist while the request is held), so the
    pick's no-request-leg arm never fires for it."""
    exchange_of = {key[0]: key[2] for key in keys}
    risk = retrieval.get_request_risk(list(exchange_of))
    states: dict[str, _State] = {}
    for iid, request in retrieval.get_exchange_requests(keys).items():
        record = None
        if iid in risk:
            r = risk[iid]
            record = {"state": "decided", "verdict": r.enforcement_type, "exchange_id": exchange_of[iid],
                      "version": r.version, "rules": r.triggered_rule_ids, "labels": []}
            if request.unread_body is not None:
                record.update(state="unjudged", reason=request.unread_body)
        states[iid] = _State(record, request.payload_hash, request.payload_stored, request.classified)
    answered = {iid: state.record for iid, state in states.items() if state.record is not None}
    blocks = {iid for iid, record in answered.items() if record["verdict"] == "block"}
    found = retrieval.get_request_findings(list(answered), content_for=blocks, max_content_bytes=REDACTED_MAX_BYTES)
    for iid, request in found.items():
        answered[iid]["labels"] = _labels(request.findings)
        if iid in blocks and request.content is not None:
            redacted = _redacted_payload(iid, request.content, request.findings)
            if redacted is not None:
                answered[iid]["redacted_payload"] = redacted
    undecided = [iid for iid, state in states.items() if state.record is None]
    if notify_undecided and undecided:
        _name_for_decision(undecided)
    return states


def _name_for_decision(ids: list[str]) -> None:
    with db.transaction() as tx:
        for iid in ids:
            tx.execute("SELECT pg_notify(%s, %s)", (PRIORITY_DECISION_CHANNEL, iid))


class PriorityClassifier:
    """Classifies a waited exchange's request payload in this process, through
    the classifier's own write-once ``process_payload``. Holds the model (loaded
    and warmed off the event loop; :attr:`warm` gates readiness), a worker pool of
    ``concurrency``, and one in-flight classification per payload hash."""

    def __init__(self, detector_factory, model_version: int, concurrency: int) -> None:
        self._factory = detector_factory
        self._model_version = model_version
        self._concurrency = concurrency
        self._exec = ThreadPoolExecutor(concurrency, thread_name_prefix="verdict-priority-classification")
        self._inflight: set[str] = set()
        self._detector = None
        self.warm = False

    def shutdown(self) -> None:
        self._exec.shutdown(wait=False, cancel_futures=True)

    def _load_and_warm(self) -> tuple[int, int]:
        from data_governance.processors.classification import verdict

        started = time.monotonic()
        self._detector = self._factory()
        loaded = time.monotonic()
        verdict.classify(_WARM_HASH, _WARM_SAMPLE, detector=self._detector, model_version=self._model_version)
        return int((loaded - started) * 1000), int((time.monotonic() - loaded) * 1000)

    async def load_and_warm(self) -> None:
        loaded_ms, sample_ms = await asyncio.get_running_loop().run_in_executor(self._exec, self._load_and_warm)
        self.warm = True
        log.info("verdict priority classification model loaded in %d ms, warm sample classified in %d ms", loaded_ms, sample_ms)

    def _classify(self, content_hash: str) -> int | None:
        from data_governance.processors.classification.driver import Payload, process_payload

        with db.transaction() as tx:
            row = tx.fetch_one(
                "SELECT p.content, p.seq, pc.content_hash IS NOT NULL "
                "  FROM interaction_payloads p "
                "  LEFT JOIN payload_classifications pc ON pc.content_hash = p.content_hash "
                " WHERE p.content_hash = %s",
                (content_hash,),
            )
            if row is None or row[2]:
                return None  # gone, or the cursor loop got there first
            started = time.monotonic()
            process_payload(
                tx,
                Payload(content_hash=content_hash, content=row[0], seq=int(row[1])),
                detector=self._detector,
                model_version=self._model_version,
            )
            return int((time.monotonic() - started) * 1000)

    def start(self, content_hash: str, done) -> None:
        """Classify *content_hash* if the model is warm, it is not in flight here
        and a worker is free; call ``done(content_hash, ms | None)`` on the loop when
        finished (``None``: it was not classified here).

        There is no queue: a payload nobody waits on any more must not run ahead
        of a gate still holding its request. A waited payload that finds every
        worker busy is offered again on the dispatcher's next pass, at most
        :data:`BACKSTOP_SECONDS` later, for as long as someone waits on it."""
        if not self.warm or content_hash in self._inflight or len(self._inflight) >= self._concurrency:
            return
        self._inflight.add(content_hash)
        future = asyncio.get_running_loop().run_in_executor(self._exec, self._classify, content_hash)

        def _finished(f: asyncio.Future) -> None:
            self._inflight.discard(content_hash)
            if f.cancelled():
                return
            exc = f.exception()
            if exc is not None:
                log.warning("verdict priority classification: classifying %s failed: %s", content_hash, exc)
                done(content_hash, None)
                return
            done(content_hash, f.result())

        future.add_done_callback(_finished)


def _log_failure(future: asyncio.Future) -> None:
    if not future.cancelled() and future.exception() is not None:
        log.warning("verdict: priority decision notify failed: %s", future.exception())


@dataclasses.dataclass(eq=False)
class _Waiter:
    key: _Key
    future: asyncio.Future
    payload_hash: str | None = None
    classify_ms: int | None = None  # set when this process classified the payload


class VerdictService:
    """The waiter map, its dispatcher, the cap and priority classification, per process. Built
    in the app's lifespan; every method runs on the event loop."""

    def __init__(self, hub, settings: Settings, priority_classifier: PriorityClassifier | None = None) -> None:
        self.settings = settings
        self.priority_classifier = priority_classifier
        self.active = 0
        self._hub = hub
        self._reads = ThreadPoolExecutor(settings.read_workers, thread_name_prefix="verdict-read")
        self._waiters: dict[str, set[_Waiter]] = {}
        self._nonempty = asyncio.Event()
        self._task: asyncio.Task | None = None
        #: Batched reads the dispatcher ran (diagnostic; what a test counts).
        self.dispatch_reads = 0

    @property
    def ready(self) -> bool:
        return self.priority_classifier is None or self.priority_classifier.warm

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._dispatch(), name="verdict-dispatch")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._reads.shutdown(wait=False, cancel_futures=True)
        if self.priority_classifier is not None:
            self.priority_classifier.shutdown()

    def authorized(self, header: str | None) -> bool:
        if not self.settings.secret:
            return True
        expected = f"Bearer {self.settings.secret}".encode()
        return hmac.compare_digest((header or "").encode(), expected)

    # -- waiting -----------------------------------------------------------

    async def _read(self, keys: list[_Key], notify_undecided: bool = False) -> dict[str, _State]:
        return await asyncio.get_running_loop().run_in_executor(self._reads, _read_states, keys, notify_undecided)

    async def wait(self, key: _Key, wait_ms: int) -> tuple[dict[str, Any], _Waiter]:
        started = time.monotonic()
        interaction_id = key[0]
        waiter = _Waiter(key, asyncio.get_running_loop().create_future())
        self._waiters.setdefault(interaction_id, set()).add(waiter)
        self._nonempty.set()
        try:
            if wait_ms:
                # The whole read-and-wait answers within wait_ms (#339): a slow or queued
                # first read is abandoned at the deadline, as the wait is.
                try:
                    await asyncio.wait_for(self._read_then_wait(key, waiter, True), timeout=wait_ms / 1000)
                except TimeoutError:
                    pass
            else:
                await self._read_then_wait(key, waiter, False)
            waited_ms = int((time.monotonic() - started) * 1000)
            if waiter.future.done():
                return {**waiter.future.result(), "waited_ms": waited_ms}, waiter
            return {"state": "pending", "exchange_id": key[2], "waited_ms": waited_ms}, waiter
        finally:
            peers = self._waiters.get(interaction_id)
            if peers is not None:
                peers.discard(waiter)
                if not peers:
                    del self._waiters[interaction_id]

    async def _read_then_wait(self, key: _Key, waiter: _Waiter, wait: bool) -> None:
        """Read the exchange's state once; with *wait*, then wait for the dispatcher to answer it."""
        self._apply(await self._read([key], self.settings.priority_decision and wait))
        if wait:
            await asyncio.shield(waiter.future)

    def _apply(self, states: dict[str, _State]) -> None:
        for interaction_id, state in states.items():
            waiters = self._waiters.get(interaction_id)
            if not waiters:
                continue
            if state.record is not None:
                for waiter in waiters:
                    if not waiter.future.done():
                        waiter.future.set_result(state.record)
                continue
            for waiter in waiters:
                waiter.payload_hash = state.payload_hash
            if self.priority_classifier is not None and state.payload_present and not state.classified:
                self.priority_classifier.start(state.payload_hash, self._classified)

    def _classified(self, content_hash: str, ms: int | None) -> None:
        ids = []
        for interaction_id, waiters in self._waiters.items():
            for waiter in waiters:
                if waiter.payload_hash == content_hash:
                    if ms is not None:
                        waiter.classify_ms = ms
                    ids.append(interaction_id)
        if self.settings.priority_decision and ids:
            future = asyncio.get_running_loop().run_in_executor(self._reads, _name_for_decision, sorted(set(ids)))
            future.add_done_callback(_log_failure)

    async def _dispatch(self) -> None:
        event = self._hub.subscribe()
        try:
            while True:
                if not self._waiters:
                    self._nonempty.clear()
                    await self._nonempty.wait()
                try:
                    await asyncio.wait_for(event.wait(), timeout=BACKSTOP_SECONDS)
                except TimeoutError:
                    pass
                # Clear before the read (live.py's rule): a write landing while
                # the read runs leaves the event set, so the next pass reads again.
                event.clear()
                keys = [next(iter(waiters)).key for waiters in self._waiters.values()]
                if not keys:
                    continue
                try:
                    states = await self._read(keys)
                except Exception as exc:  # noqa: BLE001 — a DB blip: the waits run out, the loop survives
                    log.warning("verdict dispatcher read failed (%s); retrying", exc)
                    await asyncio.sleep(BACKSTOP_SECONDS)
                    continue
                self.dispatch_reads += 1
                self._apply(states)
        finally:
            self._hub.unsubscribe(event)


def _answer(status: int, body: dict[str, Any], fields: dict[str, Any]) -> Response:
    log.info("verdict %s", json.dumps({**fields, "status": status, **{
        k: body.get(k) for k in ("state", "verdict", "reason", "waited_ms", "error") if k in body
    }}, separators=(",", ":")))
    return JSONResponse(body, status_code=status)


async def verdict_handler(request: Request) -> Response:
    service: VerdictService = request.app.state.verdict
    trace_id = request.path_params["trace_id"]
    exchange_id = request.path_params["exchange_id"]
    fields: dict[str, Any] = {"trace": trace_id[:_LOG_KEY_CHARS], "exchange": exchange_id[:_LOG_KEY_CHARS]}
    if not service.authorized(request.headers.get("authorization")):
        return _answer(401, {"error": "unauthorized"}, fields)
    if not _TRACE_RE.fullmatch(trace_id) or not _SPAN_RE.fullmatch(exchange_id):
        return _answer(400, {"error": "bad_key",
                             "message": "trace_id is 32 and exchange_id 16 lowercase hex digits"}, fields)
    raw_wait = request.query_params.get("wait_ms", "0")
    if not _WAIT_RE.fullmatch(raw_wait):
        return _answer(400, {"error": "bad_wait_ms", "message": "wait_ms must be 0..3000, in digits"}, fields)
    wait_ms = int(raw_wait)
    if wait_ms > MAX_WAIT_MS:
        return _answer(400, {"error": "wait_ms_too_large", "max": MAX_WAIT_MS}, fields)
    if service.active >= service.settings.max_waiters:
        return _answer(503, {"error": "verdict_busy"}, fields)

    interaction_id = _interaction_id(trace_id, exchange_id)
    fields["interaction_id"] = interaction_id
    service.active += 1
    try:
        body, waiter = await service.wait((interaction_id, trace_id, exchange_id), wait_ms)
    except Exception as exc:  # noqa: BLE001 — the gate reads any non-answer as verdict_error
        log.warning("verdict for interaction_id=%s failed: %s", interaction_id, exc)
        return _answer(500, {"error": "verdict_unavailable"}, fields)
    finally:
        service.active -= 1
    fields["priority_classified"] = waiter.classify_ms is not None
    fields["classify_ms"] = waiter.classify_ms
    return _answer(200, body, fields)


def routes() -> list[BaseRoute]:
    return [
        Route(
            "/enforce/v1/verdicts/{trace_id:str}/{exchange_id:str}",
            endpoint=verdict_handler,
            methods=["GET"],
        ),
    ]
