"""Migration CLI for the data-governance schema.

Per PROJECT.md §3 "Schema evolution / Invocation" the project ships a single
migration CLI: ``python -m data_governance.db.migrate`` runs ``alembic upgrade
head`` against the configured Postgres and exits zero on success. This is
invoked in k8s as an init container before the OTLP-receiver container starts
and manually in non-k8s deployments (ADR-0002).

The migration tooling lives next to the schema it manages — the Alembic
migrations under ``data_governance/db/migrations/`` — rather than inside any
one consumer (the OTLP receiver, future processors). Every consumer of the
schema runs the same CLI.
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
    migrations_dir = Path(__file__).resolve().parent / "migrations"
    cfg.set_main_option("script_location", str(migrations_dir))
    return cfg


def _upgrade(_args: argparse.Namespace) -> int:
    """Run ``alembic upgrade head`` and return 0 on success.

    Idempotent by virtue of Alembic's version table — running this twice in
    a row leaves the second invocation a no-op.
    """
    cfg = _alembic_config()
    command.upgrade(cfg, "head")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m data_governance.db.migrate",
        description="Run Alembic migrations to head against $DATABASE_URL.",
    )
    parser.set_defaults(func=_upgrade)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
