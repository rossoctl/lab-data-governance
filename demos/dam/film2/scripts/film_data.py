"""Write film/data/film-data.js from a take: its steps (who acted, when, Sentry's decision and reason
for every call), each agent's operations as Sentry decided them, and the company policy's rules.

  python3 scripts/film_data.py <take>
"""
import json
import pathlib
import shutil
import sys

root = pathlib.Path(__file__).resolve().parent.parent
take = sys.argv[1] if len(sys.argv) > 1 else "take1"
src = root / f"build/ui/{take}"
events = json.loads((src / "events.json").read_text())
sentry = json.loads((src / "sentry.json").read_text())

names = {v: k for k, v in sentry["ids"].items()}
granted = {}
for o in sentry.get("onboarded", []):
    granted[o["name"]] = {"allowed": o["allowed"], "denied": o["denied"]}
company = sentry.get("company") or {}
rules = [{"text": r["text"], "roles": r["roles"], "holders": r["holders"],
          "agents": [names.get(a, a) for a in r["agents"]],
          "operations": [o.split(".", 1)[1] for o in r["operations"]]} for r in company.get("role_rules", [])]

steps = {e["step"]: e for e in events}
# The Keycloak scene recorded on its own (ui/record_kc.mjs), on its own video's clock.
if (src / "kc-events.json").exists():
    steps["keycloak"] = json.loads((src / "kc-events.json").read_text())
data = {
    "take": take,
    "videos": {who: f"media/{take}-{who}.mp4" for who in ("kc", "alice", "bob", "dev")},
    "steps": steps,
    "granted": granted,
    "rules": rules,
    "dataRules": company.get("data_rules", []),
    "rego": {n: (src / f"rego-{n}.rego").read_text() for n in ("issue-workbench", "qa-signoff") if (src / f"rego-{n}.rego").exists()},
}
(root / "film/data/film-data.js").write_text("window.FILM = " + json.dumps(data, indent=1) + ";\n")
media = root / "film/media"
media.mkdir(parents=True, exist_ok=True)
for who in ("kc", "alice", "bob", "dev"):
    shutil.copyfile(src / f"session-{who}.mp4", media / f"{take}-{who}.mp4")
print(f"film/data/film-data.js and film/media/{take}-*.mp4 from {take}: {len(events)} steps, "
      f"{len(rules)} role rules, {sum(1 for e in events if e.get('pass') is False)} off-story")
