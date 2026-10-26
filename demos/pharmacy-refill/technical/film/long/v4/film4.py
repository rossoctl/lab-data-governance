"""film4.py — the technical film (v4.2): the 2:35 pitch as it is (its picture, film/pitch-2m35/master/sentry_pitch_demo.mp4,
from lab-dg demo/pharmacy-refill-pitch; pins.yaml) with nine
technical sections spliced into its pauses (v4/narration.yaml). Each section is a steps.js stage: the
camera pushes from the pitch's frame at the cut through a door in it, through Sentry's real pages and our panels (rings
from each page's own boxes), and pulls back out to the same frame, where the pitch resumes. Its lines are George's
(Eleven v4, common.synth, cached in tts11/), each step's still arriving just before its line.

    FILM_TAKE=film/takes/<name> $PYTHON film/long/v4/film4.py stage [I5 …]     # the named sections' stages (all when none)
    FILM_TAKE=… $PYTHON film/long/v4/film4.py join --music --onevoice --out film.mp4   # → <take>/film.mp4 (+ .plan.json)
    FILM_TAKE=… $PYTHON film/long/v4/film4.py srt --out film.mp4                       # → <take>/film.srt

Everything it reads and writes is in the take (env.TAKE): the stills, panels, live frames, counts.json, pitch-fixed.mp4
and the door frames (fixes/build4.py), stages/, out/, the film. A line not yet spoken needs ELEVENLABS_API_KEY; the rest
comes from the voice cache (film/long/tts11/). take/film.sh runs the whole chain.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import wave

import yaml

V4 = pathlib.Path(__file__).resolve().parent
LONG = V4.parent
sys.path.insert(0, str(LONG))
os.environ["VOICE_TEMPO"] = "1.12"               # George at the pitch narrator's pace (pitch kept); common reads it on import.
                                                  # Set here, not taken from the shell: a stray VOICE_TEMPO would retime the film
from common import FPS, render, sh, synth  # noqa: E402
from env import TAKE  # noqa: E402

PITCH = TAKE / "pitch-fixed.mp4"        # the pitch with its Sentry cards repainted with this take's names (fixes/build4.py)
if not PITCH.exists() and __name__ == "__main__" and sys.argv[1:2] == ["join"]:
    sys.exit(f"no {PITCH}: the pitch with this take's repainted cards (take/film.sh join builds it; or fixes/build4.py)")
COUNTS = json.loads((TAKE / "counts.json").read_text()) if (TAKE / "counts.json").exists() else {}
class _Keep(dict):
    def __missing__(self, k):        # a count not read yet stays a placeholder (a section without it still builds)
        return "{" + k + "}"


TEXT = {k: {kk: vv.format_map(_Keep(COUNTS)) for kk, vv in v.items()} for k, v in yaml.safe_load((V4 / "narration.yaml").read_text()).items()}
# the cut in the pitch (a frame inside a pause, before any dissolve; measured 10-25 night) and the door in that frame
CUT = {"I1": 246, "I2": 626, "I3": 974, "I4": 1180, "I5": 1945, "I6": 3078, "I7": 3556, "I8": 3904, "I9": 4468}
DOOR = {"I1": [180, 175, 345, 710], "I2": [108, 252, 1104, 456], "I3": [216, 537, 1488, 240], "I4": [750, 240, 420, 780],
        "I5": [780, 300, 420, 700], "I6": [600, 294, 735, 150], "I7": [48, 684, 990, 204], "I8": [1161, 108, 690, 255],
        "I9": [480, 252, 960, 534]}
FULL = [0, 0, 1920, 1080]


def boxes(d: str) -> dict:
    return json.loads((TAKE / d / "boxes.json").read_text())


def grow(b: list, pad: int = 10) -> list:
    return [b[0] - pad, b[1] - pad, b[2] + 2 * pad, b[3] + 2 * pad]


def panel(b: list) -> list:
    """A camera close enough to read a box in Sentry's drawer."""
    return [b[0] - 560, b[1] - 300, b[2] + 640, b[3] + 600]


def near(b: list) -> list:
    """A camera on a part of Sentry's graph: the exchange and its neighbours."""
    return [b[0] - 420, b[1] - 260, b[2] + 840, b[3] + 520]


def cues(keys: list[str], steps: list[dict], d: dict[str, float], first: float = 1.5, gap: float = 0.3, tail: float = 0.6):
    """One voice line per step; a step lasts its line plus a gap, at least its `hold` (default 2.2 s, long enough to
    read a ring)."""
    c, at, t = {"zoomEnd": 1.2, "steps": []}, {}, first
    for k, s in zip(keys, steps):
        c["steps"].append(round(t - 0.3, 3)); at[k] = t
        t += max(d[k] + gap, s.get("hold", 2.2))
    c["pull"] = round(t - gap + 0.5, 3); c["pullEnd"] = round(c["pull"] + 1.0, 3); c["end"] = round(c["pullEnd"] + tail, 3)
    return c, at


def section(name: str) -> tuple[dict, list[tuple[str, dict]]]:
    """The stage spec and the (line key, step) list of one section. Paths are relative to <take>/stages/<name>/."""
    up = "../.."
    S = lambda d, f: f"{up}/{d}/{f}.png"
    tap, l1 = boxes("shots/tap"), boxes("shots/level1")
    pn = boxes("panels/out")
    spec = {"front": f"{up}/src/pitch-{CUT[name]}.png", "door": DOOR[name], "drift": 0.03}
    level = {"I5": "level 1 · all context in place", "I6": "level 2 · out of context", "I7": "level 3 · another session", "I8": "the fixed app"}
    if name in level:                                    # the pitch's level badge is not on screen in a section: the tag names it
        spec["tag"] = f"UNDER THE HOOD · <i>{level[name]}</i>"
    if name == "I1":
        m = boxes("map")
        steps = [("L1", {"img": S("shots/rossoctl", "agents"), "label": "rossoctl · Agent Catalog", "sub": "the pharmacy app, deployed from the UI",
                         "ring": grow(boxes("shots/rossoctl")["agents"]["table"], 6), "tone": "gold", "side": "below"}),
                 ("L2", {"img": S("shots/rossoctl", "tools"), "ring": grow(boxes("shots/rossoctl")["tools"]["table"], 6),
                         "label": "5 agents · 8 tools", "sub": "each in its own pod, beside its sidecar", "side": "below"}),
                 ("L3", {"img": S("map", "flow"), "notag": True,
                         "ring": m["flow"]["path1"], "label": "The refill, inside the pharmacy", "sub": "look-up · clinical review · the records", "side": "right"}),
                 ("L4", {"img": S("map", "flow"), "notag": True, "ring": m["flow"]["path2"], "tone": "red", "side": "below",
                         "label": "One report leaves the pharmacy", "sub": "report-dispense → the wholesaler’s demand forecaster"})]
    elif name == "I2":
        n = len(list((TAKE / "live").glob("*.png")))
        steps = [("L1", {"img": f"{up}/live/0001.png", "seq": {"dir": f"{up}/live", "n": n}, "ff": COUNTS.get("ff", "▶▶"), "hold": n / FPS + 0.4,
                         "cam": [0, 0, 1300, 731]}),     # no ring here: its veil would dim the graph as it grows
                 ("L2", {"img": S("shots/tap", "graph"), "cam": grow(tap["nodes"], 70), "ring": grow(tap["lookPair"], 8), "label": "One exchange", "sub": "a request and its reply"}),
                 ("L3", {"img": S("shots/tap", "graph"), "cam": grow(tap["nodes"], 70), "ring": grow(tap["nodes"], 24),
                         "label": f"{COUNTS.get('N', '')} interactions · one tap", "sub": f"{COUNTS.get('T', '')} among agents and tools · {COUNTS.get('M', '')} calls to the model"}),
                 ("L4", {"img": S("shots/tap", "exchange"), "cam": panel(grow(tap["who"], 30)), "ring": grow(tap["who"], 6),
                         "label": "Who called whom", "sub": "pharmacy-assistant → lookup-patient"}),
                 ("L4b", {"img": S("shots/tap", "sent"), "cam": panel(tap["what"]), "ring": tap["what"], "label": "What was sent", "sub": "her name and birth date"}),
                 ("L5", {"img": S("shots/tap", "risk"), "cam": near(tap["red"]), "ring": tap["red"], "tone": "red",
                         "label": "The risk layer", "sub": "report-dispense → demand-forecast.example"})]
    elif name == "I3":
        b = pn["pipeline"]
        steps = [("L1", {"img": S("panels/out", "pipeline"), "notag": True, "ring": grow(b["pods"], 8), "label": "A sidecar beside each pod", "side": "right"}),
                 ("L2", {"img": S("panels/out", "pipeline"), "notag": True, "ring": grow(b["records"], 8), "label": "Two records per exchange", "side": "below"}),
                 ("L3", {"img": S("panels/out", "pipeline"), "notag": True, "ring": grow(b["s-int"], 6), "label": "Interactions", "side": "left"}),
                 ("L4", {"img": S("panels/out", "pipeline"), "notag": True, "ring": grow(b["s-cls"], 6), "label": "Classification", "side": "left"}),
                 ("L5", {"img": S("panels/out", "pipeline"), "notag": True, "ring": grow(b["s-lin"], 6), "label": "Lineage", "side": "left"}),
                 ("L6", {"img": S("panels/out", "pipeline"), "notag": True, "ring": grow([*b["s-risk"][:3], b["verdict"][1] + b["verdict"][3] - b["s-risk"][1]], 6),
                         "label": "Risk assessment", "sub": "the policy, then a verdict", "side": "left"})]
    elif name == "I4":
        r3 = json.loads((TAKE / "shots" / "policy" / "boxes.json").read_text())
        rb = lambda k: [round(r3[k]["x"]), round(r3[k]["y"]), round(r3[k]["width"]), round(r3[k]["height"])]
        p3 = f"{up}/shots/policy"
        b = pn["rego"]; ins = pn["inside"]
        steps = [("L1", {"img": f"{p3}/rules.png", "cam": [0, 120, 1920, 600], "ring": grow(rb("rules"), 4), "label": "The policy · three rules", "side": "below"}),
                 ("L2", {"img": f"{p3}/dg001.png", "cam": [0, 120, 1920, 760], "ring": grow(rb("conditions"), 6), "label": "DG-001",
                         "sub": "personal data → an untrusted external destination · suggested enforcement: block", "side": "below"}),
                 ("L3", {"img": f"{p3}/rules.png", "cam": [0, 120, 1920, 600], "ring": grow(rb("rules"), 4), "label": "DG-002 · health data · DG-004 · restricted", "side": "below"}),
                 ("L4", {"img": S("panels/out", "inside"), "notag": True, "ring": grow(ins["d-courier"], 6), "tone": "green",
                         "label": "Inside: a service of this cluster", "side": "below"}),
                 ("L4b", {"img": S("panels/out", "inside"), "notag": True, "ring": grow(ins["setting"], 6),
                          "label": "The company’s list", "sub": "a deployment setting", "side": "below"}),
                 ("L4c", {"img": S("panels/out", "inside"), "notag": True, "ring": grow(ins["d-fcst"], 6), "tone": "red",
                          "label": "Outside: external, untrusted", "side": "below"}),
                 ("L5", {"img": S("panels/out", "rego"), "notag": True, "ring": grow(b["rego"], 4), "label": "Rule DG-001 in Rego", "sub": "the module OPA serves in this cluster", "side": "below"}),
                 ("L6", {"img": S("panels/out", "rego"), "notag": True, "cam": [40, 160, 1840, 720],
                         "ring": grow([b["l-pii"][0], b["l-pii"][1], max(b["l-pii"][2], b["l-dest"][2]), b["l-dest"][1] + b["l-dest"][3] - b["l-pii"][1]], 4),
                         "label": "PII · external · untrusted", "sub": "all three in one flow: the rule fires", "side": "below"})]
    elif name == "I5":
        o = pn["opaA"]
        steps = [("L1", {"img": S("shots/level1", "red"), "cam": near(l1["red"]), "ring": l1["red"], "tone": "red", "label": "Level 1 · version A", "sub": "report-dispense → demand-forecast.example"}),
                 ("L2", {"img": S("shots/level1", "payload"), "cam": panel(l1["payload"]), "ring": l1["payload"], "label": "What was sent", "sub": "her name, birth date and number"}),
                 ("L3", {"img": S("shots/level1", "cls"), "cam": [1000, l1["found"][1] - 200, 920, 518], "ring": grow(l1["found"], 6), "label": "Classification",
                         "sub": "found here: the classifier · carried in: data lineage", "hold": 4.5}),
                 ("L4", {"img": S("shots/level1", "cls-name"), "cam": [1000, max(l1["nameRow"][1] - 240, 0), 920, 518], "ring": l1["nameRow"], "tone": "red",
                         "label": "Found here: her name", "sub": "PN · restricted · PII"}),
                 ("L5", {"img": S("panels/out", "opaA"), "notag": True, "cam": grow(o["input"], 30),
                         "ring": grow([o["i-found"][0], o["i-event"][1], o["input"][2] - 60, o["i-found"][1] + o["i-found"][3] - o["i-event"][1]], 4),
                         "label": "The input to OPA", "sub": "found here: PII · restricted", "side": "right"}),
                 ("L6", {"img": S("panels/out", "opaA"), "notag": True, "ring": grow(o["i-carried"], 4), "label": "Carried in: the number is an MRN",
                         "sub": "health data · DG-002 (level 2 shows why)", "side": "below"}),
                 ("L6b", {"img": S("panels/out", "opaA"), "notag": True, "ring": grow(o["answer"], 4), "tone": "red", "label": "OPA’s answer",
                          "sub": "critical · block · most restrictive", "side": "left"}),
                 ("L7", {"img": S("shots/level1", "http"), "cam": panel(l1["http"]), "ring": grow(l1["http"], 6), "label": "A recommendation",
                         "sub": "the alert is raised; the call went through: POST → 200"}),
                 ("L8", {"img": S("shots/level1", "courier-allow"), "cam": panel(l1["courier-allow"]), "ring": grow(l1["courier-allow"], 6), "tone": "green",
                         "label": "The courier: allowed", "sub": "same name and address · inside the organization"})]
    elif name == "I6":
        l2 = boxes("shots/level2"); o = pn["opaB"]
        hop = lambda k, t, s: {"img": S("shots/level2", "tracked"), "cam": grow(l2[k], 230), "ring": grow(l2[k], 6), "label": t, "sub": s, "hold": 3.8}
        steps = [("L1", {"img": S("shots/level2", "payload"), "cam": panel(l2["payload"]), "ring": l2["payload"], "label": "Level 2 · version B", "sub": "only the number"}),
                 ("L2", {"img": S("shots/level2", "readings"), "cam": panel(l2["readings"]), "ring": l2["alone"], "label": "Read alone: an operational ID", "sub": "OPID · internal · found here"}),
                 ("L3", {"img": S("shots/level2", "tracked"), "cam": grow(l2["nodes"], 60), "ring": grow(l2["nodes"], 20), "label": "Track 4471820",
                         "sub": "every exchange that carried it, lit"}),
                 ("L4", {**hop("hop1", "Hop 1 · the label is born", "lookup-patient’s answer: her name + her MRN"), "tone": "gold"}),
                 ("L5", hop("hop2", "Hop 2", "pharmacy-assistant → fulfillment-agent")),
                 ("L6", hop("hop3", "Hop 3", "fulfillment-agent → report-dispense")),
                 ("L7", {**hop("hop4", "Hop 4", "report-dispense → demand-forecast.example"), "tone": "red"}),
                 ("L8", {"img": S("shots/level2", "readings"), "cam": panel(l2["readings"]), "ring": l2["carried"], "tone": "red",
                         "label": "Carried in: her MRN", "sub": "MRN · restricted · PHI, PII · matched on the exact value"}),
                 ("L9", {"img": S("panels/out", "opaB"), "notag": True, "ring": grow([o["i-carried"][0], o["i-carried"][1], o["answer"][0] + o["answer"][2] - o["i-carried"][0], max(o["i-carried"][3], o["answer"][3])], 4),
                         "tone": "red", "label": "Same three rules", "sub": "critical · block", "side": "below"})]
    elif name == "I7":
        nb = boxes("shots/night")
        steps = [("L1", {"img": S("shots/night", "list"), "cam": [0, 60, 1920, 560], "ring": grow(nb["rowN"], 4), "label": "The nightly batch", "sub": "its own session, its own trace", "side": "below"}),
                 ("L2", {"img": S("shots/night", "write"), "cam": panel(nb["writePayload"]), "ring": nb["writePayload"], "label": "Back in Rosa’s refill",
                         "sub": "review-refill → records · record_fill · account_ref 4471820"}),
                 ("L3", {"img": S("shots/night", "answer"), "cam": panel(nb["readings"]), "ring": nb["alone"], "label": "The records’ answer, at night", "sub": "read alone: OPID"}),
                 ("L4", {"img": S("shots/night", "cross"), "cam": panel(nb["cross"]), "ring": grow(nb["cross"], 6), "tone": "red", "label": "Carried in across sessions",
                         "sub": "her MRN · origin: the patient lookup, in the trace of Rosa’s refill"}),
                 ("L5", {"img": S("shots/night", "verdict"), "cam": panel(nb["verdict"]), "ring": grow(nb["verdict"], 6), "tone": "red",
                         "label": "Critical · recommend block", "sub": "DG-001 · DG-002 · DG-004"})]
    elif name == "I8":
        fb = boxes("shots/fixed")
        steps = [("L1", {"img": S("shots/fixed", "c-payload"), "cam": panel(fb["cPayload"]), "ring": fb["cPayload"], "tone": "green", "label": "The fixed app", "sub": "the drug and the dates · no patient key"}),
                 ("L2", {"img": S("shots/fixed", "c-allow"), "cam": panel(fb["c-allow"]), "ring": grow(fb["c-allow"], 6), "tone": "green", "label": "Allowed", "sub": "no rule fires · no alert"}),
                 ("L3", {"img": S("shots/fixed", "n2-allow"), "cam": panel(fb["n2-allow"]), "ring": grow(fb["n2-allow"], 6), "tone": "green", "label": "The nightly batch: allowed",
                         "sub": "fixed the same way"}),
                 ("L4", {"img": S("shots/fixed", "c-payload"), "cam": FULL, "label": "Find the leak · check the fix"})]
    elif name == "I9":
        g = pn["grid"]
        steps = [("L1", {"img": S("panels/out", "grid"), "notag": True, "ring": grow(g["grid"], 8), "hold": 5.5})]
    return spec, steps


def rel(w) -> str:
    """A voice file as the plans keep it: relative to film/long (the voice cache), so a take's plans work on any machine."""
    return os.path.relpath(w, LONG)


def wav(w: str) -> str:
    return w if os.path.isabs(w) else str(LONG / w)


def stage(name: str) -> dict:
    spec, steps = section(name)
    for _, st in steps:                                  # a dense drawing of ours: the ring's surroundings darker
        if st.get("notag") and "ring" in st:
            st.setdefault("veil", 0.66)
    here = TAKE / "stages" / name; here.mkdir(parents=True, exist_ok=True)
    keys = [k for k, _ in steps]
    E = {k: synth(TEXT[name][k]) for k in keys}
    # a short hold on the pitch's frame after the pull-out (most cuts sit just before the pitch's own dissolve); none
    # after I9, whose frame dissolves straight into the end card
    c, at = cues(keys, [s for _, s in steps], {k: v[1] for k, v in E.items()}, tail=0.0 if name == "I9" else 0.6)
    full = {**spec, "back": spec["front"], "backDoor": spec["door"], "steps": [s for _, s in steps]}
    (here / "stage.html").write_text("<!doctype html>\n<!-- generated by v4/film4.py; edit the build, not this file -->\n"
                                     f"<html><head><meta charset=\"utf-8\"></head><body>\n<script>const SPEC = {json.dumps(full, indent=1)};</script>"
                                     f"<script src=\"{os.path.relpath(LONG / 'steps.js', here)}\"></script></body></html>\n")
    out = TAKE / "out"; out.mkdir(exist_ok=True)
    render(here / "stage.html", c, out / f"{name}.stage.mp4")
    plan = {"cues": c, "lines": [[rel(E[k][0]), round(at[k], 3), round(E[k][1], 3), TEXT[name][k]] for k in keys]}
    (out / f"{name}.plan.json").write_text(json.dumps(plan, indent=1))
    print(f"{name}: {c['end']:.1f} s, {len(keys)} lines")
    return plan


# The one-voice version (one voice for the whole film): the pitch's lines re-voiced by George, each sentence a
# beat whose picture is retimed to its new line (common.beat's rule: scaled within 1.3x, then its last frame held or its
# tail cut). The pitch's words are kept, except that a block is said as a recommendation (Sentry recommends) and a few
# words as the voice must say them.
SAY = {"Level 1.": "Level one.", "Level 2. The app now sends only the number.": "Level two. The app now sends only the number.",
       "it's classified as a generic operational ID.": "it's classified as a generic operational I D.",
       "Sending them there breaks the policy, so Sentry blocks it.": "Sending them there breaks the policy, so Sentry recommends blocking it.",
       "It's still personal data, so sending it breaks the policy, and it's blocked.":
           "It's still personal data, so sending it breaks the policy, and Sentry recommends blocking it.",
       "With full context, Sentry blocks.": "With full context, Sentry recommends a block.",
       "so this leak is blocked too.": "so this leak is caught too.",
       "Sentry doesn't fix the app, it enforces the policy.": "Sentry doesn't fix the app, it applies the policy."}
MAX_K = 1.3


def beats(a: int, b: int) -> list[dict]:
    """The pitch's frames [a, b) as beats: one per sentence (lines ending in a comma join the next), each from the middle
    of the pause before it to the middle of the pause after it."""
    pitch = [s for s in json.loads((LONG.parent / "pitch-2m35" / "transcript.json").read_text()) if a / FPS <= s["start"] < b / FPS]
    sent = []
    for s in pitch:
        if sent and sent[-1]["text"].rstrip().endswith(","):
            sent[-1] = {"start": sent[-1]["start"], "end": s["end"], "text": sent[-1]["text"] + " " + SAY.get(s["text"], s["text"]), "src": sent[-1]["src"] + [s["text"]]}
        else:
            sent.append({"start": s["start"], "end": s["end"], "text": SAY.get(s["text"], s["text"]), "src": [s["text"]]})
    edges = [a / FPS] + [(sent[i - 1]["end"] + sent[i]["start"]) / 2 for i in range(1, len(sent))] + [b / FPS]
    out = []
    for j, s in enumerate(sent):
        x0, x1 = edges[j], edges[j + 1]
        wav, d = synth(s["text"])
        lead, air = max(s["start"] - x0, 0.2), max(x1 - s["end"], 0.35)
        want = lead + d + air
        k = min(max(want / (x1 - x0), 1 / MAX_K), MAX_K)
        out.append({"a": x0, "b": x1, "k": k, "len": want, "wav": str(wav), "lead": lead * k, "text": s["text"], "src": s["src"]})
    return out


def lufs(f: pathlib.Path | str) -> float:
    o = subprocess.run(["ffmpeg", "-nostats", "-i", str(f), "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True).stderr
    return float([l for l in o.splitlines() if l.strip().startswith("I:")][-1].split()[1])


def join(music: bool, out: pathlib.Path, onevoice: bool = False) -> None:
    names = list(CUT)
    plans = {n: json.loads((TAKE / "out" / f"{n}.plan.json").read_text()) for n in names}
    end = int(round(float(sh(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=nb_frames", "-of", "csv=p=0", str(PITCH)]))))
    bounds = [0] + [CUT[n] for n in names] + [end]
    ins, vf, af, lines, t = ["-i", str(PITCH)], [], [], [], 0.0
    pieces = []
    spoken = []                                          # the words in film order (review.py reads "script")
    seglen = {}                                          # each segment's length in this film (the score is stretched to it)
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        if onevoice:
            for j, bt in enumerate(beats(a, b)):
                f = f"[0:v]trim=start={bt['a']:.4f}:end={bt['b']:.4f},setpts=(PTS-STARTPTS)*{bt['k']:.4f},fps={FPS}"
                extra = bt["len"] - (bt["b"] - bt["a"]) * bt["k"]
                if extra > 0:
                    f += f",tpad=stop_mode=clone:stop_duration={extra:.3f}"
                vf.append(f + f",trim=0:{bt['len']:.4f},setpts=PTS-STARTPTS,format=yuv420p[pv{i}_{j}]")
                af.append(f"anullsrc=r=48000:cl=mono,atrim=0:{bt['len']:.4f}[pa{i}_{j}]")
                pieces.append((f"[pv{i}_{j}]", f"[pa{i}_{j}]")); lines.append((bt["wav"], t + bt["lead"])); spoken.append(bt["text"])
                t += bt["len"]; seglen[f"P{i}"] = seglen.get(f"P{i}", 0) + bt["len"]
        else:
            vf.append(f"[0:v]trim=start_frame={a}:end_frame={b},setpts=PTS-STARTPTS,fps={FPS},format=yuv420p[pv{i}]")
            af.append(f"[0:a]atrim=start={a / FPS:.4f}:end={b / FPS:.4f},asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=mono,"
                      f"afade=t=in:d=0.02,areverse,afade=t=in:d=0.02,areverse[pa{i}]")
            pieces.append((f"[pv{i}]", f"[pa{i}]")); t += (b - a) / FPS; seglen[f"P{i}"] = (b - a) / FPS
            spoken += [s["text"] for s in json.loads((LONG.parent / "pitch-2m35" / "transcript.json").read_text()) if a / FPS <= s["start"] < b / FPS]
        if i < len(names):
            n = names[i]; c = plans[n]["cues"]
            ins += ["-i", str(TAKE / "out" / f"{n}.stage.mp4")]; k = len(ins) // 2 - 1
            vf.append(f"[{k}:v]setpts=PTS-STARTPTS,fps={FPS},format=yuv420p,trim=0:{c['end']:.4f}[sv{i}]")
            af.append(f"anullsrc=r=48000:cl=mono,atrim=0:{c['end']:.4f}[sa{i}]")
            pieces.append((f"[sv{i}]", f"[sa{i}]"))
            lines += [(wav(w), t + at) for w, at, _, _ in plans[n]["lines"]]; t += c["end"]; seglen[n] = c["end"]
            spoken += [txt for _, _, _, txt in plans[n]["lines"]]
    # George at the pitch narrator's loudness: one gain for every inserted line, from the two speech tracks (one voice: none)
    pitch_lufs = lufs(PITCH)
    cat = TAKE / "out" / "george-all.wav"
    lst = TAKE / "out" / "george-all.txt"; lst.write_text("".join(f"file '{w}'\n" for w, _ in lines))
    sh(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-ar", "48000", "-ac", "1", str(cat)])
    gain = 0.0 if onevoice else pitch_lufs - lufs(cat)
    n_v = len(ins) // 2
    mix = []
    for j, (w, at) in enumerate(lines):
        ins += ["-i", w]; mix.append(f"[{n_v + j}:a]aresample=48000,volume={gain:.2f}dB,adelay={int(at * 1000)}:all=1[g{j}]")
    fc = vf + af + ["".join(v + a for v, a in pieces) + f"concat=n={len(pieces)}:v=1:a=1[v][base]"] + mix + \
        ["[base]" + "".join(f"[g{j}]" for j in range(len(lines))) + f"amix=inputs={len(lines) + 1}:normalize=0:duration=first[voice]"]
    final = "[voice]"
    if music:
        import score
        ins += ["-i", str(score.track(seglen, "score-onevoice.mp3" if onevoice else "score.mp3"))]
        fc.append(f"[{len(ins) // 2 - 1}:a]aresample=48000,loudnorm=I=-30:TP=-4:LRA=9,atrim=0:{t:.3f}[m];"
                  f"[voice]asplit[vo][key];[m][key]sidechaincompress=threshold=0.02:ratio=6:attack=60:release=500[duck];"
                  f"[vo][duck]amix=inputs=2:normalize=0:duration=first[mixed]")
        final = "[mixed]"
    fc.append(f"{final}loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[a]")
    script = TAKE / "out" / "join.filter"; script.write_text(";\n".join(fc))
    sh(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex_script", str(script), "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
        "-crf", "17", "-preset", "slow", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-movflags", "+faststart", str(out)])
    out.with_suffix(".plan.json").write_text(json.dumps({"script": spoken, "onevoice": onevoice, "film_lines": [[rel(w), round(at, 3)] for w, at in lines],
                                                         "cuts": CUT, "george_gain_db": round(gain, 2), "pitch_lufs": pitch_lufs,
                                                         "sections": {n: plans[n] for n in names}}, indent=1))
    print(f"{out.name}: {t:.1f} s planned; George {gain:+.1f} dB to the pitch's {pitch_lufs:.1f} LUFS")


def srt(film: pathlib.Path) -> None:
    """film.srt from film.plan.json: the pitch's lines (its transcript, moved by the sections before them) and the
    sections' lines at their own times; at most two lines of 46 characters per cue."""
    import textwrap
    plan = json.loads(film.with_suffix(".plan.json").read_text())
    pitch = json.loads((LONG.parent / "pitch-2m35" / "transcript.json").read_text())
    cuts, secs = list(plan["cuts"].items()), plan["sections"]
    cues, shift = [], 0.0
    if plan.get("onevoice"):                             # one voice: every spoken line is in the film's own line list
        for (w, at), txt in zip(plan["film_lines"], plan["script"]):
            with wave.open(wav(w)) as f:
                cues.append((at, at + f.getnframes() / f.getframerate(), txt))
        pitch, cuts = [], []
    for s in pitch:
        before = [n for n, c in cuts if c / FPS <= s["start"]]
        shift = sum(secs[n]["cues"]["end"] for n in before)
        cues.append((s["start"] + shift, s["end"] + shift, s["text"]))
    t, prev = 0.0, 0
    for n, c in cuts:
        t += (c - prev) / FPS; prev = c
        cues += [(t + at, t + at + d, txt) for _, at, d, txt in secs[n]["lines"]]
        t += secs[n]["cues"]["end"]
    ts = lambda x: f"{int(x // 3600):02d}:{int(x % 3600 // 60):02d}:{int(x % 60):02d},{int(round(x % 1 * 1000)) % 1000:03d}"
    out, k = [], 0
    spell = {"Rosso C T L": "rossoctl", "D G one": "DG-001", "D G two": "DG-002", "D G four": "DG-004", "O P A": "OPA",
             "operational I D": "operational ID", "P I I": "PII", "Red alone": "Read alone"}       # the voice's spellings, written as the screen writes them
    for a, b, txt in sorted(cues):
        for k0, v0 in spell.items():
            txt = txt.replace(k0, v0)
        chunks = textwrap.wrap(txt, 46)
        parts = [chunks[i:i + 2] for i in range(0, len(chunks), 2)]
        n_chars = sum(len(" ".join(p)) for p in parts)
        t0 = a
        for p in parts:                                  # a long line splits into cues, timed by their share of the words
            t1 = t0 + (b - a) * len(" ".join(p)) / n_chars
            k += 1; out.append(f"{k}\n{ts(t0)} --> {ts(t1)}\n" + "\n".join(p) + "\n"); t0 = t1
    film.with_suffix(".srt").write_text("\n".join(out))
    print(f"{film.with_suffix('.srt').name}: {k} cues")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["stage", "join", "srt"]); ap.add_argument("names", nargs="*")
    ap.add_argument("--music", action="store_true"); ap.add_argument("--onevoice", action="store_true"); ap.add_argument("--out", default="long-v4.mp4")
    a = ap.parse_args()
    if a.cmd == "stage":
        for n in a.names or list(CUT):
            stage(n)
    elif a.cmd == "srt":
        srt(TAKE / a.out)
    else:
        o = TAKE / a.out
        if o.exists():
            raise SystemExit(f"{o} exists; old outputs are never overwritten")
        join(a.music, o, a.onevoice)
