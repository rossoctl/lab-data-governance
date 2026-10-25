# Demos

A demo here is a scripted, repeatable walk through one data-governance scenario on a running
cluster: a runbook with the exact commands and their real outputs, the presenter's script, and
the small tools the commands use. Nothing under `demos/` is imported by the package or the tests;
it is operator tooling.

| demo | what it shows |
|---|---|
| [`pharmacy-refill/`](pharmacy-refill/README.md) | a pharmacy app that sends a patient's record number to an external vendor, and DG catching it at three depths of context: in the message, four hops back by label lineage, and from another session. So far the scenario and its 2:35 pitch film (`pitch/`) |
