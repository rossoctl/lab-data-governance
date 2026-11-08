# Demos

A demo here is a scripted, repeatable walk through one data-governance scenario on a running
cluster: a runbook with the exact commands and their real outputs, the presenter's script, and
the small tools the commands use. Nothing under `demos/` is imported by the package or the tests;
it is operator tooling. Each tool writes its outputs to its own `runs/` directory (gitignored).

| demo | what it shows |
|---|---|
| [`risk/rogue/`](risk/rogue/RUNBOOK.md) | a booking agent whose instructions make it send a guest's name to an external partner, the rule that fires (DG-001, DG-004), and the instruction change that stops it — one env var on one agent (#258, #259) |
