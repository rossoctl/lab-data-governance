"""Receiver CLI entry point.

Per PROJECT.md §3 "Schema evolution / Invocation" the receiver ships a single
CLI: ``python -m data_governance.receiver migrate`` runs ``alembic upgrade
head`` against the configured Postgres and exits zero on success. This is
invoked in k8s as an init container before the receiver container starts and
manually in non-k8s deployments (ADR-0002).

The receiver process itself (OTLP socket, span-write path) lands in
subsequent issues — for now this module only knows about ``migrate``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config


def _alembic_config() -> Config:
    """Load alembic.ini from the repo root.

    The config file's location is computed relative to this package so the
    CLI works regardless of the caller's working directory (k8s init
    containers, local dev runs).
    """
    repo_root = Path(__file__).resolve().parents[2]
    cfg_path = repo_root / "alembic.ini"
    cfg = Config(str(cfg_path))
    # Ensure script_location resolves correctly even when alembic.ini's
    # relative path is interpreted from the wrong cwd.
    migrations_dir = repo_root / "data_governance" / "db" / "migrations"
    cfg.set_main_option("script_location", str(migrations_dir))
    return cfg


def _migrate(_args: argparse.Namespace) -> int:
    """Run ``alembic upgrade head`` and return 0 on success.

    Idempotent by virtue of Alembic's version table — running this twice in
    a row leaves the second invocation a no-op.
    """
    cfg = _alembic_config()
    command.upgrade(cfg, "head")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m data_governance.receiver",
        description="Data-governance receiver CLI.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_migrate = sub.add_parser(
        "migrate",
        help="Run Alembic migrations to head against $DATABASE_URL.",
    )
    p_migrate.set_defaults(func=_migrate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
