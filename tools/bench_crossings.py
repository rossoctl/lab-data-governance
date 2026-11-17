"""Hand-run benchmark of label lineage across conversations.

Not a test and not a gate: it prints what the mechanism does at scale, against a real
Postgres (testcontainers, the migrated schema) and the production derivation
(``driver.drain``, the store, ``cross.Memory``). No number here sets a threshold.

1. ``constant``: a persistent partner receives one identifier in each of 200
   conversations and returns it there; each return is matched against what it received
   in the conversations before. By the rules (M = 3, retention 15 min, R1,
   R2): conversations 2-4 draw 1, 2, 3 crossing edges; from conversation 5 on, none and
   a common-value count. What is loaded per derivation is what is valid at the send:
   at one conversation a minute, the 14 conversations of the last 15 minutes; at one
   every 2 s nothing expires inside 200 conversations, so it grows to 199.
2. ``many``: a partner holds 100,000 values (or 100) and sends a 10 kB payload with
   five of them. The load reads by the hash of every run of 1..K payload words (K the
   most words among the values the partner holds valid at the send), so exactly the held values present, whatever their
   count or shape; the match costs (start boundaries x distinct held lengths) lookups.

    export DOCKER_HOST=unix://$(podman machine inspect --format '{{.ConnectionInfo.PodmanSocket.Path}}')
    TESTCONTAINERS_RYUK_DISABLED=true .venv/bin/python -m tools.bench_crossings          # both
    TESTCONTAINERS_RYUK_DISABLED=true .venv/bin/python -m tools.bench_crossings constant # or many
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import statistics
import subprocess
import sys
import time

import psycopg
from psycopg.types.json import Jsonb
from testcontainers.postgres import PostgresContainer

from data_governance import db
from data_governance.label_lineage import cross, find, fold
from data_governance.processors.classification.projection import project
from data_governance.processors.label_lineage import driver

T0 = dt.datetime(2026, 11, 13, 9, 0, tzinfo=dt.timezone.utc)
CONFIG = driver.Config(find.exact, "exact", crossing=cross.Crossing())
SSN = "123-45-6789"


class Seed:
    """Writes conversations as the upstream processors would (entities, interactions, legs, payloads, findings)."""

    def __init__(self, conn: psycopg.Connection, entities: dict[str, str]) -> None:
        self.conn, self.seq = conn, 0
        for eid, kind in entities.items():
            conn.execute("INSERT INTO entities (id, kind, natural_key, display_name, detected_from, original_seq) "
                         "VALUES (%s, %s, %s, %s, 'observed', 1)", (eid, kind, f"{kind}:{eid}", eid))

    def conversation(self, trace: str, t: dt.datetime, caller: str, callee: str, sent: object, returned: object,
                     sent_marks: list[tuple[str, str]]) -> None:
        """One call: *caller* sends *sent* (classified with *sent_marks*), *callee* returns *returned* 1 s later."""
        ix = f"{trace}-ix"
        self.conn.execute("INSERT INTO interactions (id, trace_id, caller_entity_id, callee_entity_id, summary) "
                          "VALUES (%s, %s, %s, %s, '')", (ix, trace, caller, callee))
        for n, (leg, content, marks) in enumerate((("request", sent, sent_marks), ("response", returned, []))):
            h, text = f"h-{trace}-{leg}", project(content)
            findings, at = [], 0
            for kind, value in marks:
                at = text.index(value, at)
                findings.append({"entity_type": kind, "text": value, "start": at, "end": at + len(value),
                                 "regulatory_tags": ["PII"], "sensitivity_level": "RESTRICTED"})
            self.conn.execute("INSERT INTO interaction_payloads (content_hash, content, byte_size) VALUES (%s, %s, %s)",
                              (h, Jsonb(content), len(json.dumps(content))))
            self.conn.execute(
                "INSERT INTO payload_classifications (content_hash, sensitivity_level, regulatory_tags, "
                "contains_identity_bundle, is_personalized, primary_domain, findings, model_version) "
                "VALUES (%s, 'RESTRICTED', '{}', false, false, null, %s, 1)", (h, Jsonb(findings)))
            self.seq += 1
            self.conn.execute("INSERT INTO interaction_legs (interaction_id, leg_type, seq, occurred_at, payload_hash, "
                              "error) VALUES (%s, %s, %s, %s, %s, false)",
                              (ix, leg, self.seq, t + dt.timedelta(seconds=n), h))
        self.conn.commit()


@contextlib.contextmanager
def measured():
    """Wrap the driver's derivation and its load of priors to record, per trace, the time and the rows loaded
    (instrumentation only; the originals are put back after)."""
    seen: dict[str, dict] = {"derive_s": {}, "loaded": {}}
    real_derive, real_load = driver.derive_and_persist, driver.load_priors

    def derive(tx, trace_id, config, *rest):
        t = time.perf_counter()
        out = real_derive(tx, trace_id, config, *rest)
        seen["derive_s"][trace_id] = time.perf_counter() - t
        return out

    def load(tx, trace_id, *a, **k):
        out = real_load(tx, trace_id, *a, **k)
        seen["loaded"][trace_id] = len(out)
        return out

    driver.derive_and_persist, driver.load_priors = derive, load
    try:
        yield seen
    finally:
        driver.derive_and_persist, driver.load_priors = real_derive, real_load


def rows(dsn: str, sql: str) -> list[tuple]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(sql).fetchall()


def constant(dsn: str, gap_s: int, n: int = 200) -> None:
    print(f"\n## one constant in {n} conversations, one every {gap_s} s (retention 900 s, M 3)")
    print("| conv | derive ms | median ms (last 10) | loaded | crossing edges | common count |")
    print("|---:|---:|---:|---:|---:|---:|")
    cursor, times = 0, []
    with measured() as seen, psycopg.connect(dsn) as conn:
        seed = Seed(conn, {"a": "agent", "v": "service"})
        for i in range(1, n + 1):
            trace = f"C{i:04d}"
            seed.conversation(trace, T0 + dt.timedelta(seconds=gap_s * i), "a", "v",
                              {"patient": {"ssn": SSN, "visit": i}}, {"subject": SSN, "visit": i}, [("SSN", SSN)])
            cursor = driver.drain(cursor, CONFIG)
            times.append(seen["derive_s"][trace])
            if i in (2, 3, 4, 5, 10, 15, 16, 50, 100, 150, 200):
                (edges,), = rows(dsn, f"SELECT count(*) FROM label_crossings c JOIN payload_label_edges e "
                                      f"USING (edge_id) WHERE c.trace_id = '{trace}'")
                common = rows(dsn, f"SELECT conversations FROM label_crossings WHERE trace_id = '{trace}' "
                                   f"AND edge_id IS NULL")
                print(f"| {i} | {times[-1] * 1e3:.1f} | {statistics.median(times[-10:]) * 1e3:.1f} | "
                      f"{seen['loaded'][trace]} | {edges} | {common[0][0] if common else '-'} |")
    (occ,), = rows(dsn, "SELECT count(*) FROM label_occurrences")
    print(f"derivations: {len(seen['derive_s'])} traces derived for {n} conversations; occurrence rows: {occ}")


def many(dsn: str) -> None:
    print("\n## a 10 kB payload against what the partner holds (five held values in it)")
    shapes = {"ssn": lambda k: f"{100 + k % 900:03d}-{10 + k // 900 % 90:02d}-{1000 + k // 81000:04d}",
              "mrn": lambda k: f"MRN-{k:07d}"}
    parts = {f"{s}-{size}": (s, size) for s in shapes for size in (100, 100_000)}
    filler = "the courier left the parcel at the front desk and signed the slip "
    texts = {}
    with psycopg.connect(dsn) as conn:
        seed = Seed(conn, {"a": "agent", **{p: "service" for p in parts}})
        for p, (s, size) in parts.items():         # registered 1,000 values a conversation, all inside the retention
            values = [shapes[s](k) for k in range(size)]
            for b in range(0, size, 1000):
                batch = values[b:b + 1000]
                seed.conversation(f"R-{p}-{b // 1000:04d}", T0 + dt.timedelta(seconds=b // 1000), "a", p,
                                  {"batch": batch}, {"ok": True}, [(s.upper(), v) for v in batch])
        cursor = driver.drain(0, CONFIG)
        for p, (s, size) in parts.items():
            held = [shapes[s](k) for k in (1, size // 3, size // 2, size - 7, size - 1)]
            # kept apart by "; ": the value reader reads numbers joined by a space or a dash as one number
            report = "; ".join(held[:2]) + "; " + (filler * 200)[:10_000 - 15 * len(held)] + "; " + "; ".join(held[2:])
            texts[p] = project({"report": report})
            seed.conversation(f"S-{p}", T0 + dt.timedelta(seconds=300), "a", p, {"q": "report"}, {"report": report}, [])
    with measured() as seen:
        driver.drain(cursor, CONFIG)
    print("| partner | holdings | payload bytes | K | hashes | loaded rows | load ms | fold ms | derive ms | crossings |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for p, (s, size) in parts.items():
        loads, folds = [], []
        for _ in range(5):                         # load and fold again, read only, for a median
            with db.transaction() as tx:
                t = time.perf_counter()
                trace = driver.load_trace(tx, f"S-{p}", CONFIG)
                loads.append(time.perf_counter() - t)
                t = time.perf_counter()
                d = fold.derive(trace, CONFIG.finder, CONFIG.scopes, CONFIG.crossing)
                folds.append(time.perf_counter() - t)
        (k,), = rows(dsn, f"SELECT max(word_count) FROM label_occurrences WHERE entity_id = '{p}'")
        print(f"| {p} | {size:,} | {len(texts[p].encode()):,} | {k} | {len(cross.window_hashes(texts[p], k)):,} | "
              f"{len(trace.priors):,} | {statistics.median(loads) * 1e3:.1f} | "
              f"{statistics.median(folds) * 1e3:.1f} | {seen['derive_s'][f'S-{p}'] * 1e3:.1f} | {len(d.levels)} |")


def main(argv: list[str]) -> int:
    which = argv[1:] or ["constant", "many"]
    with PostgresContainer("postgres:16-alpine") as pg:
        admin = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        for name in [w for w in ("constant", "many") if w in which]:
            for variant in ((60,), (2,)) if name == "constant" else ((),):
                dbname = f"bench_{name}_{'_'.join(map(str, variant)) or 'x'}"
                with psycopg.connect(admin, autocommit=True) as conn:
                    conn.execute(f'CREATE DATABASE "{dbname}"')
                dsn = admin.rsplit("/", 1)[0] + "/" + dbname
                subprocess.run([sys.executable, "-m", "data_governance.db.migrate"], check=True, capture_output=True,
                               env={"DATABASE_URL": dsn, "PATH": "/usr/bin:/bin"})
                db.close_pool()
                db.configure(dsn)
                (constant if name == "constant" else many)(dsn, *variant)
                db.close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
