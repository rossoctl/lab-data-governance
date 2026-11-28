# Sentry on DAM, the second film: intent and company policy

The story of the PRD (`~/development/.handoff/2026-11-26-sentry-dam-intent-and-roles-prd.md`), as a narrated film like `../film/`, whose pipeline it reuses.
Sentry is one product on screen: reasons name the agent's description or the company policy, never aiac or dg.

## Scenes

| # | Scene | Picture |
| --- | --- | --- |
| 1 | Title | still d01 |
| 2 | DAM and Sentry | architecture diagram, with what this work adds (who asks, the company policy) |
| 3 | The cast | Alice Mayer (developer), Bob Segal (tester), the two agents and their descriptions |
| 4 | The agents in DAM | UI: each agent's description and People (Bob an editor) |
| 5 | Act 1, DAM as it is | UI: five requests, all go through |
| 6 | Sentry on | UI: the Sentry switch; bubble: each agent's operations from its description |
| 7 | Act 2, descriptions | UI: six requests; two gaps left |
| 8 | The company security policy | UI: dev writes it on the Operator page; bubble: its rules, roles from Keycloak |
| 9 | Act 3, plus the policy | UI: five requests |
| 10 | Summary | table: the requests by act |
| 11 | Close | logo card |

## Pipeline

1. `ui/record.mjs <take>`: three browsers (alice, bob, dev); the acting person's page is captured ten times a second; events name the user and carry Sentry's decisions.
2. `scripts/film_data.py <take>` -> `film/data/film-data.js`.
3. `scripts/narration.txt` -> `scripts/tts.py` -> `scripts/fit_holds.py` -> `scripts/tts.py` -> `scripts/cues.sh`.
4. `film/film.html` + `film/timeline.js`, rendered by `scripts/render.mjs` (PW_FROM: DAM's Playwright) and muxed by `scripts/mux.sh` into `sentry_intent_and_policy.mp4`.

## Status

- [x] recorder, take (take2: every act step as the story says)
- [x] narration, voice (fitted to take2)
- [x] timeline and scenes (stills reviewed)
- [x] render, mux, review (take4; reviews 1 to 4 applied)

## Review 1 (2026-11-26), done

1. Opening as in the first film: DAM works well; we enhance it with granular access control and data governance.
2. Keycloak's users and roles on screen: Alice is a developer, Bob a tester.
3. The two agents, their descriptions read out, and each one's job explained plainly, before the acts.
4. DAM as both people side by side, the login screen included.
5. Every yellow result explained: why it is not green (against the agent's description, or against what the company would want, such as the tester verifying a fix and closing).
6. The pace of the first film: prompts typed slower, time to read them and the answers.
7. The company security policy narrated as it is typed.
8. The split, said plainly: "Sentry takes in the security policy and builds the corresponding rules: access control rules, for developers and testers, and data governance rules, for personal data."
9. Each rule once (the "only testers" sentence appeared twice), the rules on the left and their Rego on the right.
10. Say "the user and role", never "who asks".
