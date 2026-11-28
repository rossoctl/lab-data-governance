# Sentry on DAM, the second film: intent and company policy

A 10:03 narrated film (`sentry_intent_and_policy.mp4`) of the combined Sentry on DAM demo.
Alice Mayer (developer) and Bob Segal (tester) share two DAM agents, `issue-workbench` and `qa-signoff`, in three acts:

1. **DAM as it is.** Every request goes through.
2. **Sentry, from each agent's description.** Each agent keeps to its job, down to what one tool does.
3. **Sentry, plus the company security policy**, written once on DAM's Operator page. Access follows the user and role, from Keycloak; which data may leave follows the data's classification.

The DAM UI is a real recorded session: Keycloak's console, then Alice's and Bob's browsers side by side (the person acting in the larger pane), and the operator's; the film around it is an HTML timeline rendered frame by frame.
`PLAN.md` lists the scenes.

| Path | What it is |
| --- | --- |
| `ui/record.mjs` | Drives Keycloak's console and DAM's UI through the story as the realm admin, Alice, Bob and the operator; records one video per person to `build/ui/<take>/`; checks every act step against the story |
| `ui/record_kc.mjs` | Records the Keycloak scene on its own, for a take whose Keycloak stream came out short |
| `scripts/film_data.py` | Writes `film/data/film-data.js` from a take (steps, decisions and reasons, each agent's operations, the policy's rules) and copies its video to `film/media/` |
| `scripts/narration.txt` | The narration, one line per beat |
| `scripts/tts.py`, `scripts/fit_holds.py`, `scripts/cues.sh` | Speak the narration with Kokoro, fit the holds to the take, expose the timings to the film |
| `scripts/render.mjs`, `scripts/mux.sh` | Render `film/film.html` in headless Chrome, then add the narration at -16 LUFS |
| `overlays/`, `stills/` | Links to the first film's fonts and stills |

## Rebuilding

From a DAM install set up with `../film/ui/setup.mjs` (agents, people, connection, Sentry deployed):

```sh
export KC_ADMIN_PASSWORD=...   # Keycloak's admin password, from the cluster's platform-keycloak-secrets
(cd ui && PW_FROM=~/development/dam/packages/e2e/playwright node record.mjs take4)
python3 scripts/film_data.py take4
~/.venvs/kokoro/bin/python scripts/tts.py scripts/narration.txt voice af_heart
python3 scripts/fit_holds.py take4 && ~/.venvs/kokoro/bin/python scripts/tts.py scripts/narration.txt voice af_heart
scripts/cues.sh
(cd scripts && PW_FROM=~/development/dam/packages/e2e/playwright node render.mjs && ./mux.sh)
```

The recorder resets `s-and-p-team/demo-public#1` (reopens it, clears its labels and comments) before each act.
