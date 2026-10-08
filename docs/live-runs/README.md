# Live run reports

Rendered by `python3 tests/live/report.py <run dir>` from a run record. The records themselves,
under `tests/live/runs/`, are gitignored: they hold payload-bearing rows. A report starts with
preflight (every pin, expected and observed), capabilities and the catalog proof, then one table
of check rows per scenario with its expectation card: the audit with its KNOWN gaps, the forest
against the plan for the lab, the per-leg verdicts, the ledger join.

Keep one file per run worth keeping: the run that established a baseline, the run that found
something. `report.py --csv` writes the same rows as CSV, and `report.py diff <A> <B>` shows what
changed between two records.

| report | app | what it is |
|---|---|---|
| `travel_advisor.md`, `.csv` | travel_advisor | the baseline: 14 pass, L13 skip (no alerts processor); 478 rows, no FAIL, 4 KNOWN: the content-kind gap (L1, L6) and the stale-callee record (#279) on the risk scenarios. L14 passed on this run (the rogue booking agent made the partner call; the record is critical/block); on a turn where the model does not make the call the row xfails and records the booking statuses |
| `travel_advisor-r2-on-risk.md` | travel_advisor | L8, L16 and L17 alone against data governance as `risk` ships it (2026-10-09): every new row red for its reason — L8's trailing-dot FQDN and ClusterIP decide critical/block and the public IPv6 literal none/allow, while every Host arrives as sent; L16 finds two processes in the drain loop (`[2, 2]`); L17 does not settle, leg-ready in CrashLoopBackOff on `ValueError: Invalid IPv6 URL` with 212 legs held. The five original L8 probes pass |
| `travel_advisor-r2-with-fixes.md` | travel_advisor | the full run with `E2E_DESTRUCTIVE=1` against data governance carrying the round-2 fixes (the three fix branches merged on `risk`): L8, L12, L16, L17 and the rest of group B pass; the deploy also cleared the leg L17 had stuck. L2, L6 and L15 fail on one ledger row, `get-flights → minio GET`, which the app's ledger does not record: the fleet on the cluster ran a shim re-baked with the MCP context bridge, which joins that exchange to the turn; no data-governance code is involved |
| `lineage_lab.md`, `.csv` | lineage_lab | the baseline: 9 pass with the forest equal to the plan on every row, R8 xfail (the classifier reads a redacted record's last four digits as PCI); 576 rows, no FAIL, 3 KNOWN (#279) |

Both were made on 2026-10-08 by following `docs/LIVE-E2E.md` from three fresh clones: app `main`
at `642167f`, cortex `main` at `9de6574`, data governance built from this checkout on `risk` after #283 (migration
head `0020`, no override), both app namespaces created from nothing and then re-attached once with
`deploy-app.sh` (its re-run path) before the runs. The `DG` value in a header is `git rev-parse
HEAD` of the checkout at run time; a report is committed after its run, so that value names the
working-tree commit the run was made from, not a commit of this branch's history.
