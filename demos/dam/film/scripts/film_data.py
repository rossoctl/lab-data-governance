"""Write film/data/film-data.js from a take: its step events, the agent id, and aiac's Rego modules."""
import json, pathlib, sys
root = pathlib.Path(__file__).resolve().parent.parent
take = sys.argv[1] if len(sys.argv) > 1 else "take1"
events = json.loads((root / f"build/ui/{take}/events.json").read_text())
data = {
    "take": take,
    "video": f"media/{take}.mp4",
    "steps": {e["step"]: e for e in events},
    "agentId": (root / "film/data/agent_id.txt").read_text().strip(),
    "regoV1": (root / "film/data/rego_v1.rego").read_text(),
    "regoV2": (root / "film/data/rego_v2.rego").read_text(),
    "calls": json.loads((root / "film/data/calls.json").read_text()),
}
(root / "film/data/film-data.js").write_text("window.FILM = " + json.dumps(data, indent=1) + ";\n")
print("film/data/film-data.js from", take, "-", len(events), "steps")
