# The pharmacy-refill demo — one rule, three depths of context

Rosa taps **Refill my prescription** in her pharmacy's mobile app. The tap runs an agentic flow of 26
agent and tool interactions, and one of them, the `enroll-adherence` tool, sends patient data to an
external host, `api.adherence-insights.example:8000`. The demo takes one rule from the company
policy, *personal data must not leave the organization*, and shows DG (Sentry) holding it at three
depths of context: in the message itself, four hops back in the same trace, and from another
session. All patient data in it is synthetic.

This directory holds the scenario and, in [`pitch/`](pitch/README.md), the 2:35 narrated film made
from it for a non-technical audience. The technical demo belongs beside them, as `RUNBOOK.md` (the
exact commands and their real outputs) and `SCRIPT.md` (the presenter's script), the way the
risk demo (`demos/risk/rogue/`, on the `risk` branch) does it.

## The app

As DG's flow graph shows it (`pitch/overlays/ui/exec_flow_full.png`, from a run on 2026-10-19):

| role | names |
|---|---|
| client | `demo-client` |
| agents | `pharmacy-assistant`, `clinical-review-agent`, `fulfillment-agent` |
| tools | `lookup-patient`, `review-refill`, `enroll-adherence`, `schedule-delivery` |
| LLM | `qwen2.5:7b`, served from `host.containers.internal` |
| external hosts | `api.adherence-insights.example:8000` (the vendor); a courier API (`api.courier….est.me:8000`, cut off in the graph) |

Rosa's record, as `lookup-patient` returns it: name `Rosa Delgado`, MRN `4471820`, date of birth,
phone, address, plan member ID (`pitch/overlays/ui/lookup_reply.png`).

## The three levels

| | what leaves for the vendor | classified locally as | the film's verdict, and what it rests on |
|---|---|---|---|
| ① all context in place | Rosa's name and the number, with her date of birth, drug and dates | `PN` (person name) and `OPID` → RESTRICTED, identity bundle, PI/PII | block: a critical `pii_to_untrusted_external` alert (DG-001, with DG-004 `restricted_external_sharing`). The message alone is enough: local enforcement, which most DG tools do |
| ② out of context | the number only, with drug and dates | `OPID`, a generic operational ID — local enforcement lets it through | block. Label lineage finds `4471820` born as an MRN (RESTRICTED, PHI/PII) at #203801, the `pharmacy-assistant → lookup-patient` response, four hops back |
| ③ another session | a nightly job reads the number from a shared database: no name, no context | `OPID` | block. Cross-session lineage traces the label to Rosa's refill, in another trace |
| fixed app | `drug`, `fill_date`, `refill_due` — no patient key | — | allow: no alert for the trace |

The screenshot behind each cell is listed in [`pitch/README.md`](pitch/README.md#the-screenshots).

## Before showing it live

The film is a pitch, and in two places it runs ahead of the code. Check both on the build you demo.

- **Block or alert.** The film says *blocked*. On the 2026-10-19 run DG raised a critical alert;
  the film's *blocked* assumes enforcement in the data path, planned for the next release.
- **Level 3.** The film shows the cross-session leak blocked, on the understanding that
  cross-session lineage lands with the next release. Confirm it is on your branch before showing
  level 3 live.

What the technical demo needs that is not in this repo:

- **The app.** Its agents, tools and manifests: **TODO: where it lives.**
- **The DG build.** The screenshots come from rossoctl `v0.8.0-alpha.2`, with the DG UI at
  `dg.localtest.me:8081`. Label lineage and the risk rules they show (`pii_to_untrusted_external`)
  are on the `risk` branch, not yet on `main` (as of 2026-10-25). **TODO: the DG commit.**
- **The recording.** The 7.6-minute technical walk-through the film was cut from
  (`sentry_demo_0.4.mp4`, 72 MB, not committed). **TODO: link.**
