"""env.py — where the film's parts are, for every script in film/long: the package, the take being built, Playwright,
and the cluster (from ../../film.env; film.env wins over the shell for the cluster, the shell's FILM_TAKE picks the take).

    FILM_TAKE=film/takes/<name>   the take a build reads and writes (default film/takes/v4.2, the take film v4.2 was made from)
"""
from __future__ import annotations

import os
import pathlib
import subprocess

LONG = pathlib.Path(__file__).resolve().parent          # film/long: the code, the voice cache, the music
FILM = LONG.parent                                      # film/: the pitch, the takes
PKG = FILM.parent


def _env() -> dict[str, str]:
    f = pathlib.Path(os.environ.get("FILM_ENV", PKG / "film.env"))
    if not f.exists():
        return dict(os.environ)
    out = subprocess.run(["bash", "-c", f'set -a; . "{f}"; env -0'], capture_output=True, text=True, check=True).stdout
    return dict(kv.split("=", 1) for kv in out.split("\0") if "=" in kv)


ENV = _env()
_take = os.environ.get("FILM_TAKE")
TAKE = (pathlib.Path(_take) if pathlib.Path(_take).is_absolute() else PKG / _take).resolve() if _take else FILM / "takes" / "v4.2"
NODE_PATH = str(PKG / "node_modules")                   # npm install in the package root (RUNBOOK § 0)
PORT = ENV.get("HTTP_PORT", "8080")
DG = f"http://dg.localtest.me:{PORT}"
KUBE = ["kubectl", "--kubeconfig", ENV.get("KUBECONFIG", str(pathlib.Path.home() / ".kube" / "config")),
        "--context", f"kind-{ENV.get('CLUSTER', 'sentry-film')}"]


def dgsql(q: str) -> str:
    """Run SQL on Sentry's database in the take's cluster (read-only use only); IDS_PSQL is a test hook (a psql on a dump)."""
    cmd = (ENV["IDS_PSQL"].split() if ENV.get("IDS_PSQL") else
           KUBE + ["-n", "data-governance", "exec", "-i", "data-governance-postgres-0", "--", "sh", "-c",
                   'psql -U "$POSTGRES_USER" -d data_governance -At -v ON_ERROR_STOP=1'])
    return subprocess.run(cmd, input=q, capture_output=True, text=True, check=True).stdout
