"""Entrypoint behaviour for the P-classification processor (issue #77).

Boots ``python -m data_governance.processors.classification`` as a subprocess
and asserts the process-boundary contract (exit codes, clean SIGTERM), mirroring
the P-interactions entrypoint tests (``tests/processors/interactions/
test_driver.py``) and the receiver's startup-check tests: the entrypoint reads
``DATABASE_URL``, runs the defence-in-depth schema-version check (ADR-0002), and
drives the shared wake-driven drain loop until SIGINT/SIGTERM.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import psycopg


def _spawn(dsn: str | None) -> subprocess.Popen[str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LOG_LEVEL": "INFO",
        "DB_POOL_MIN_SIZE": "1",
        "DB_POOL_MAX_SIZE": "2",
        "DB_POOL_TIMEOUT": "5",
        # Distinct metrics port so a co-located interactions processor (or a
        # parallel test) does not collide on the default.
        "CLASSIFICATION_METRICS_PORT": "0",
        # Test-only override (issue #79): skip the in-process NER-model load at
        # startup. The dev/test environment does not install torch/transformers
        # (they live behind the ``classification`` extra, only in the
        # classification image) nor materialize the ~500 MB git-LFS weights, so
        # the subprocess-boundary tests here run the loop with the no-op
        # NullDetector default. Production (the classification image) leaves this
        # unset and loads the real model — see ``__main__._build_detector``.
        "CLASSIFICATION_SKIP_MODEL": "1",
    }
    if dsn is not None:
        env["DATABASE_URL"] = dsn
    return subprocess.Popen(
        [sys.executable, "-m", "data_governance.processors.classification"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_entrypoint_exits_2_without_database_url() -> None:
    proc = _spawn(None)
    rc = proc.wait(timeout=10)
    assert rc == 2
    assert "DATABASE_URL" in (proc.stderr.read() if proc.stderr else "")


def test_entrypoint_exits_3_on_schema_mismatch(migrated_dsn: str) -> None:
    """DB forced to a stale revision → process refuses to start (ADR-0002).

    Pins the documented contract shared with the receiver / P-interactions
    entry points: a ``SchemaVersionMismatch`` exits with code 3 specifically
    (not merely non-zero), and the actionable error names *both* the stale DB
    revision and the compiled head so an operator sees what's wrong at a glance.
    This is the CrashLoopBackOff signal k8s surfaces (#81 acceptance).
    """
    from data_governance.db.schema_version import compiled_head

    stale = "0000_stale_rev"
    compiled = compiled_head()
    with psycopg.connect(migrated_dsn) as conn:
        conn.execute("UPDATE alembic_version SET version_num = %s", (stale,))
    proc = _spawn(migrated_dsn)
    rc = proc.wait(timeout=10)
    stderr = proc.stderr.read() if proc.stderr else ""
    assert rc == 3, f"expected SchemaVersionMismatch exit 3, got {rc}\nstderr:\n{stderr}"
    assert stale in stderr, f"stale revision not named in stderr:\n{stderr}"
    assert compiled in stderr, f"compiled head not named in stderr:\n{stderr}"


def test_entrypoint_exits_nonzero_when_alembic_version_missing(pg_dsn: str) -> None:
    """Migrations never ran (no ``alembic_version`` table) → the process refuses
    to start with an actionable message rather than crashing opaquely later
    (ADR-0002; mirrors the receiver's startup-check coverage). This is the
    "init container forgotten" failure mode."""
    proc = _spawn(pg_dsn)
    rc = proc.wait(timeout=10)
    stderr = proc.stderr.read() if proc.stderr else ""
    assert rc != 0, f"expected non-zero exit on missing schema; got {rc}\nstderr:\n{stderr}"
    assert "alembic_version" in stderr, f"missing-table message not actionable:\n{stderr}"


def test_entrypoint_runs_then_exits_clean_on_sigterm(migrated_dsn: str) -> None:
    """Clean path: starts against a migrated DB, runs the wake loop, exits 0 on
    SIGTERM."""
    proc = _spawn(migrated_dsn)
    # Give it a moment to pass the schema check and enter the loop.
    time.sleep(2.0)
    assert proc.poll() is None, (
        "processor exited prematurely:\n"
        f"stderr:\n{proc.stderr.read() if proc.stderr else ''}"
    )
    proc.send_signal(signal.SIGTERM)
    rc = proc.wait(timeout=10)
    assert rc == 0, f"stderr:\n{proc.stderr.read() if proc.stderr else ''}"


# --- in-process: the entrypoint builds + injects the NER model (issue #79) ---
#
# These run in-process (not a subprocess) so the heavy model load is stubbed and
# no torch is needed. They pin that the entry point constructs the in-process NER
# model ONCE at startup (ADR-0023) and injects it — with its model_version — into
# the drain loop, and that the documented test-only skip override falls back to
# the no-op NullDetector default.


def test_build_detector_constructs_the_in_process_model(monkeypatch) -> None:
    """``_build_detector`` returns a real :class:`ModelDetector` (the in-process
    NER model, ADR-0023) when the skip override is absent — this is what the
    classification image runs."""
    from data_governance.processors.classification import __main__ as entry
    from data_governance.processors.classification import detector as det_mod

    monkeypatch.delenv("CLASSIFICATION_SKIP_MODEL", raising=False)
    # Stub the heavy load so no torch/weights are needed in this test.
    monkeypatch.setattr(
        det_mod.ModelDetector,
        "_load",
        staticmethod(
            lambda model_dir, base_tokenizer: ("m", "t", {0: "O"}, "cpu", False)
        ),
    )

    detector = entry._build_detector()
    assert isinstance(detector, det_mod.ModelDetector)


def test_build_detector_honours_the_test_only_skip_override(monkeypatch) -> None:
    """With ``CLASSIFICATION_SKIP_MODEL`` set, ``_build_detector`` returns ``None``
    (→ the drain's no-op NullDetector default) so the model-free dev/test
    environment can boot the loop. Production never sets it."""
    from data_governance.processors.classification import __main__ as entry

    monkeypatch.setenv("CLASSIFICATION_SKIP_MODEL", "1")
    assert entry._build_detector() is None


def test_main_injects_the_detector_and_model_version_into_run(monkeypatch) -> None:
    """``main`` loads the detector at startup and threads it — with the model
    generation it writes (image tag ↔ ``model_version``; ADR-0023) — into
    ``driver.run``, so every drained payload is classified through the in-process
    model. Stubs out the DB, schema check, metrics server, and signal wiring so
    only the injection wiring is exercised."""
    from data_governance.processors.classification import __main__ as entry

    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h:5432/db")
    monkeypatch.setattr(entry.db, "configure", lambda dsn: None)
    monkeypatch.setattr(entry.db, "close_pool", lambda: None)
    monkeypatch.setattr(entry, "check_schema_version", lambda: None)
    monkeypatch.setattr(signal, "signal", lambda *a, **k: None)

    class _NoopMetrics:
        host = "127.0.0.1"
        port = 0

        def __init__(self, *a, **k) -> None:
            pass

        def start(self) -> None:
            pass

        def stop(self, grace: float = 0.0) -> None:
            pass

    monkeypatch.setattr(entry, "MetricsServer", _NoopMetrics)

    sentinel_detector = object()
    monkeypatch.setattr(entry, "_build_detector", lambda: sentinel_detector)

    captured: dict = {}

    def _fake_run(stop_event, dsn, detector=None, model_version=1):
        captured["detector"] = detector
        captured["model_version"] = model_version

    monkeypatch.setattr(entry.driver, "run", _fake_run)

    rc = entry.main()
    assert rc == 0
    assert captured["detector"] is sentinel_detector, (
        "main must inject the startup-loaded detector into driver.run"
    )
    assert captured["model_version"] == entry.MODEL_VERSION
