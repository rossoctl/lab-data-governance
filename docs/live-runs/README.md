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
| `travel_advisor.md`, `.csv` | travel_advisor | the baseline (2026-10-13, kit cortex `e63a4f98`, app `d3528ec`, DG built from this checkout on `risk` 52315c1): 14 pass, 3 skips (the lab module, L13 no alerts processor, L16 no sole-consumer lock — #290 open), L14 xfail (the model did not make the partner call this turn; the 2026-10-12 run had it pass: critical/block [DG-001, DG-004]); 510 rows, no FAIL, 8 KNOWN: the content-kind gap (L1, L6) and the stale-callee record (#279) on the risk scenarios. L17 green on this DG (#292 on `risk`): the truncated host classifies, no leg-ready restart. The S3 read by get-flights is a traced hop on this kit (urllib3 instrumented since cortex #1323) and pairs with the ledger's store record (`ledger.store_pairs` 1 on L2/L6/L15). The run before this one, same day, went red on L2: a freshly rolled get-weather probed `/healthz` before its first `/weather` call and the join paired the probe with the real call's span (fixed: `fix(live): a bypassed probe never takes the span of the call after it`) |
| `travel_advisor-r2-on-risk.md` | travel_advisor | L8, L16 and L17 alone against data governance as `risk` shipped it on 2026-10-09: every new row red for its reason — L8's trailing-dot FQDN and ClusterIP decide critical/block and the public IPv6 literal none/allow, while every Host arrives as sent; L16 finds two processes in the drain loop (`[2, 2]`); L17 does not settle, leg-ready in CrashLoopBackOff on `ValueError: Invalid IPv6 URL` with 212 legs held. The five original L8 probes pass |
| `travel_advisor-r2-with-fixes.md` | travel_advisor | the full run with `E2E_DESTRUCTIVE=1` against data governance carrying the round-2 fixes (the three fix branches merged locally on `risk`, #290's `fix/risk-two-consumers` among them): L8, L12, L16, L17 and the rest of group B pass; the deploy also cleared the leg L17 had stuck. L2, L6 and L15 failed on one ledger row, `get-flights → minio GET`: the fleet ran a kit that instruments urllib3 (cortex #1323), so the S3 read became a traced hop the join did not yet pair with the ledger's store record (paired since this branch's `fix(live): a declared store act over http pairs with the ledger's store record`); no data-governance code is involved |
| `lineage_lab.md`, `.csv` | lineage_lab | the baseline (2026-10-13, same kit, app and DG): 9 pass with the forest equal to the plan on every row, R8 xfail (the classifier reads a redacted record's last four digits as PCI); 575 rows, no FAIL, 1 KNOWN (R8's classifier finding; the #279 stale-callee row passes on `risk` 52315c1). R11's pod-lifetime call derives under the turn on this kit (cortex #1333), `forest.rides_turn` green |

The two baselines were made on 2026-10-12 by following `docs/LIVE-E2E.md` from pinned clones: app `main`
at `d3528ec`, cortex `main` at `e63a4f98` (the merge of #1333), data governance built from this branch merged
on `risk` 52315c1 (migration head `0020`, no override); the lab namespace re-attached through `deploy-app.sh`
(its re-run path) before its run, the travel namespace left as the previous run attached it. The `DG` value in a header is `git rev-parse
HEAD` of the checkout at run time; a report is committed after its run, so that value names the
working-tree commit the run was made from, not a commit of this branch's history.
