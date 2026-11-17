"""Write the trunk cases (C1–C6, K0–K30) and the #348 controls (P1–P3) as case.yaml files. Run once; the files
are the record, this script is how they were produced.

    .venv/bin/python -m tests.system.trunk_cases
"""

from __future__ import annotations


import yaml


class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True

from .cases import CASES_DIR

UNITS = {"s1": "SSN", "s2": "MRN", "s3": "DOB", "s4": "DIS"}
SEEN = "s1 s2 s3"                               # keys the model recognises: ssn, mrn, dob
MISS = "s1@subject s2@account s3@ref"           # keys the model must not recognise
THREE = ["s1", "s2", "s3"]
MISS_KEY = {"s1": "s1@subject", "s2": "s2@account", "s3": "s3@ref", "s4": "s4@code", "s5": "s5@who",
            "s6": "s6@since"}
TYPE = {"s1": "SSN", "s2": "MRN", "s3": "DOB", "s4": "DIS", "s5": "PN", "s6": "DOB"}

TRIVIAL = "Its deliberate break ({}) turns it red."


def legs(spec: dict[int, str], flag: int | None = None) -> str:
    """The trunk with payload contents by leg number; a leg not in *spec* is ``[-]``."""
    names = {1: "U>A", 2: "A>L", 3: "L>A", 4: "A>T1", 5: "T1>V1", 6: "V1>T1", 7: "T1>A", 8: "A>L", 9: "L>A",
             10: "A>T2", 11: "T2>V2", 12: "V2>T2", 13: "T2>A", 14: "A>L", 15: "L>A", 16: "A>U"}
    out = []
    for i in range(1, 17):
        body = spec.get(i, "-")
        if body != "-":
            body = f"{body} -"
        out.append(f"{names[i]}[{body}]" + ("⚑" if i == flag else ""))
    return ", ".join(out)


def chain(path: list[int], units=THREE) -> list[str]:
    return [f"{u} {a}>{b}" for u in units for a, b in zip(path, path[1:])]


def held_earlier(at: int, to: int, units=THREE) -> list[str]:
    """The edges from an earlier holding the agent remembers, beside the chain: the agent holds the same bytes
    twice (the LLM echoed them), so its next request inherits from both holdings."""
    return [f"{u} {at}>{to}" for u in units]


def crossing(frm: int, to: int, units, level: str) -> list[str]:
    return [f"{u} {frm}>{to} {level}" for u in units]


def every(leg_numbers, units, *kinds) -> dict[int, dict[str, list[str]]]:
    return {i: {u: list(kinds) for u in units} for i in leg_numbers} if units else {}


def received(at: int, units, types=TYPE) -> dict[int, dict[str, list[str]]]:
    """R8: the type the classifier gave each unit at the leg the partner received it, inherited there too."""
    return {at: {u: [f"classified {types[u]}", "inherited"] for u in units}}


def v(off, conv, cross) -> dict:
    return {"off": off, "conv": conv, "cross": cross}


def case(id_, title, line, *, verdict, edges, must_see, must_miss, notes, cell, pending=None, authored=False,
         units=UNITS, cross=(), evidence=None, marks=None, scopes=None, config=None, steps=None) -> None:
    expect = {"verdict": verdict, "edges": edges}
    expect.update({k: val for k, val in (("cross", list(cross)), ("evidence", evidence), ("marks", marks),
                                         ("pending", pending)) if val})
    d = {"id": id_, "title": title, "cell": cell, "line": line, "units": units,
         **{k: val for k, val in (("scopes", scopes), ("config", config), ("steps", steps)) if val},
         "expect": expect,
         "classifier": {"must_see": {str(k): v for k, v in must_see.items()},
                        "must_miss": {str(k): v for k, v in must_miss.items()}, "authored": authored},
         "notes": notes}
    if not verdict:
        del d["expect"]["verdict"]
    for key in ("evidence", "marks"):
        if d["expect"].get(key):
            d["expect"][key] = {str(k): cells for k, cells in d["expect"][key].items()}
    p = CASES_DIR / id_
    p.mkdir(exist_ok=True)
    (p / "case.yaml").write_text(yaml.dump(d, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=120))


# --- the two conversations of the K cases ---------------------------------------------------------------------
# A conversation is sixteen payloads; the n-th conversation's leg i is 16 * (n - 1) + i.

def sends(units: list[str], keys: dict | None = None) -> str:
    """K0's conversation 1: T1's answer (leg 7, the hop to V1 unseen) carries *units* on to V2 at leg 11."""
    b = " ".join((keys or {}).get(u, u) for u in units) + " -"
    return (f"U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[{b}], A>L[{b}], L>A[{b}], A>T2[{b}], T2>V2[{b}], "
            f"V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-]")


def returns(units: list[str], tool="T2", partner="V2", on=("T3", "V3"), flag=True, answer: str | None = None) -> str:
    """K0's conversation 2: *partner* returns *units* (leg 6 of the conversation) under keys the model does not
    recognise, and the agent carries them on to the second partner (leg 11, flagged); *answer* is the partner's
    answer when it differs."""
    b = " ".join(MISS_KEY[u] for u in units) + " -"
    a = answer or b
    t, w = on
    return (f"U>A[-], A>L[-], L>A[-], A>{tool}[-], {tool}>{partner}[-], {partner}>{tool}[{a}], {tool}>A[{b}], "
            f"A>L[{b}], L>A[{b}], A>{t}[{b}], {t}>{w}[{b}]{'⚑' if flag else ''}, {w}>{t}[-], {t}>A[-], A>L[-], "
            f"L>A[-], A>U[-]")


def sent_edges(base: int, units) -> list[str]:
    """The conversation's own edges of :func:`sends` at offset *base*."""
    return chain([base + i for i in (7, 8, 9, 10, 11)], units) + held_earlier(base + 7, base + 10, units)


def carried(base: int, units) -> list[str]:
    """The conversation's edges of :func:`returns` at offset *base*, from the partner's answer on."""
    return chain([base + i for i in (6, 7, 8, 9, 10, 11)], units) + held_earlier(base + 7, base + 10, units)


def sent_premise(base: int, units) -> dict[int, list[str]]:
    return {base + i: list(units) for i in (7, 8, 9, 10, 11)}


def returned_premise(base: int, units) -> dict[int, list[str]]:
    return {base + i: list(units) for i in (6, 7, 8, 9, 10, 11)}


# --- the compact conversations of K19 and K20 -----------------------------------------------------------------

def to_partner(times: int = 1) -> str:
    """Six payloads (ten for three calls): the user's identifier goes through T2 to V2, *times* calls in one
    call of T2."""
    calls = ", ".join(["T2>V2[s1 -], V2>T2[-]"] * times)
    return f"U>A[s1 -], A>T2[s1 -], {calls}, T2>A[-], A>U[-]"


def back_from_partner() -> str:
    """Ten payloads: V2 returns the identifier unrecognised; the agent sends it on to V3 (leg 7, flagged)."""
    b = "s1@subject -"
    return (f"U>A[-], A>T2[-], T2>V2[-], V2>T2[{b}], T2>A[{b}], A>T3[{b}], T3>V3[{b}]⚑, V3>T3[-], T3>A[-], "
            f"A>U[-]")


def main() -> None:
    all4 = ["s1", "s2", "s3", "s4"]
    bb, ba, aa = v("block", "block", "block"), v("allow", "block", "block"), v("allow", "allow", "allow")
    crossed_block = v("allow", "allow", "block")

    case("C1", "quiet", legs({}, flag=5), cell="trunk/one/quiet",
         verdict=aa, edges=[], must_see={}, must_miss={},
         notes="No route. Nothing sensitive is on the wire; the control for the rule and the harness.")

    case("C2", "seen throughout", legs({1: "s1 s2 s3 s4", 2: SEEN, 3: SEEN, 4: SEEN, 5: SEEN}, flag=5),
         cell="trunk/one/root-to-left-leaf", verdict=bb, edges=chain([1, 2, 3, 4, 5]) + held_earlier(1, 4),
         must_see={1: all4, 2: THREE, 3: THREE, 4: THREE, 5: THREE}, must_miss={},
         notes="Root to the left leaf, U>A>T1>V1. The classifier labels the three on every leg; lineage draws the chain, "
               "the classifier alone already decides.")

    case("C3", "lost after the prompt", legs({1: "s1 s2 s3 s4", 2: SEEN, 3: MISS, 4: MISS, 5: MISS}, flag=5),
         cell="trunk/one/root-to-left-leaf", verdict=ba, edges=chain([1, 2, 3, 4, 5]) + held_earlier(1, 4),
         must_see={1: all4, 2: THREE}, must_miss={3: THREE, 4: THREE, 5: THREE}, authored=True,
         notes="Same route as C2. From the LLM's answer on the facts travel under keys the model does not recognise; "
               "only lineage carries the label to V1.")

    case("C4", "held for the second vendor", legs({1: "s1 s2 s3 s4", 8: SEEN, 9: MISS, 10: MISS, 11: MISS}, flag=11),
         cell="trunk/one/root-to-right-leaf", verdict=ba, edges=chain([1, 8, 9, 10, 11]) + held_earlier(1, 10),
         must_see={1: all4, 8: THREE}, must_miss={9: THREE, 10: THREE, 11: THREE}, authored=True,
         notes="Root to the right leaf, the left branch benign: legs 4 to 7 carry nothing, the agent held the facts "
               "across a clean exchange.")

    case("C5", "returned by the first vendor", legs({6: "s1 s2 s3 s4", 7: SEEN, 8: MISS, 9: MISS, 10: MISS, 11: MISS}, flag=11),
         cell="trunk/one/leaf-to-leaf", verdict=ba, edges=chain([6, 7, 8, 9, 10, 11]) + held_earlier(7, 10),
         must_see={6: all4, 7: THREE}, must_miss={8: THREE, 9: THREE, 10: THREE, 11: THREE}, authored=True,
         notes="Leaf to leaf, starting from a response: born in the insurer's answer. T1 forgets, so leg 7 inherits leg 6 "
               "only through the invocation-scope fix of #348 (a response inherits what its child calls returned).")

    # C6: ..V1.. stands for legs 5 and 6 (invisible); numbering stays the trunk's, the verdict at 11.
    c6 = (f"U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[{SEEN} -], A>L[{MISS} -], L>A[{MISS} -], "
          f"A>T2[{MISS} -], T2>V2[{MISS} -]⚑, V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-]")
    case("C6", "returned unseen", c6, cell="trunk/one/leaf-to-leaf (leaf invisible)", verdict=ba,
         edges=chain([7, 8, 9, 10, 11]) + held_earlier(7, 10), must_see={7: THREE},
         must_miss={8: THREE, 9: THREE, 10: THREE, 11: THREE}, authored=True,
         notes="C5 with V1 off the wire: T1 is the leaf, the origin is T1's answer at leg 7; legs 5 and 6 are the unseen hop.")

    # K0: conversation 1 is C6's flow with no verdict (the three leave through V2 at leg 11); ten minutes later the
    # same agent runs conversation 2 (legs 17-32), C5's shape through a third tool and partner, carrying no value.
    conv1 = sends(THREE)
    conv2 = ("U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[-], T2>A[-], A>L[-], L>A[-], "
             "A>T3[-], T3>V3[-]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]")
    case("K0", "nothing crosses", f"{conv1} ~10min> {conv2}", cell="trunk/cross",
         verdict=aa, edges=sent_edges(0, THREE), must_see=sent_premise(0, THREE), must_miss={},
         notes="Two conversations of one agent, ten minutes apart; the second carries no value. Nothing crosses: "
               "no edge reaches conversation 2 and V3 is allowed.")

    # K1-K7: conversation 2 is the same, but partner V2 returns some of conversation 1's values (under keys the model
    # does not recognise), so only a crossing from conversation 1 can produce the alert at V3. V2 is an external
    # service, persistent by default: the crossing attaches at its own answer (11>22), then the conversation carries
    # it, the agent holding it twice (23>26, as C5 has 7>10). One identifier alone is value-only, an identifier with
    # another value of its record corroborated; a date alone draws nothing (R5).
    ks = {
        "K1": (THREE, "reborn, all three", "co"),
        "K2": (["s1"], "reborn, the SSN alone", "vo"),
        "K3": (["s2"], "reborn, the MRN alone", "vo"),
        "K4": (["s3"], "reborn, the birthdate alone", None),
        "K5": (["s1", "s2"], "reborn, SSN and MRN", "co"),
        "K6": (["s1", "s3"], "reborn, SSN and birthdate", "co"),
        "K7": (["s2", "s3"], "reborn, MRN and birthdate", "co"),
    }
    for id_, (k, title, level) in ks.items():
        line = f"{conv1} ~10min> {returns(k)}"
        common = dict(cell="trunk/cross", edges=sent_edges(0, THREE), must_see=sent_premise(0, THREE),
                      must_miss=returned_premise(16, k), authored=True)
        if level is None:
            case(id_, title, line, verdict=aa, evidence={**received(11, k), **every([22], k)}, **common,
                 notes=f"K0 with V2 returning {' '.join(k)} in conversation 2. No anchoring identifier among the "
                       "returned values: no crossing, allow. " + TRIVIAL.format("the anchor rule off"))
            continue
        cross = crossing(11, 22, k, level) + carried(16, k)
        conv2_legs = range(22, 28)
        case(id_, title, line, verdict=crossed_block, cross=cross,
             evidence={**received(11, k), **every(conv2_legs, k, f"crossed {level}")},
             **common,
             notes=f"K0 with V2 returning {' '.join(k)} in conversation 2. The classifier labels nothing in "
                   f"conversation 2, so only a crossing from conversation 1 can produce the alert at V3. V2 received "
                   f"{' '.join(k)} at leg 11 labelled {', '.join(TYPE[u] for u in k)} (R8); "
                   + ("one numbered identifier alone: value-only, and it blocks (R5)." if level == "vo" else
                      "an identifier with another value of the same record: corroborated."))

    # P: the two pictures of #348 as controls on the memoryless rule (a tool keeps one memory per call).
    p1 = ("U>A[s1 -], A>T1[s1 -], T1>V1[s1 -], V1>T1[s2 -], T1>V2[s1@subject s2@account -]⚑, V2>T1[-], "
          "T1>A[s1@subject s2@account -], A>U[-]")
    case("P1", "one call, two partners", p1, cell="trunk/control/one-call-two-partners", verdict=ba,
         edges=["s1 1>2", "s1 2>3", "s1 2>5", "s1 2>7", "s2 4>5", "s2 4>7"],
         must_see={1: ["s1"], 2: ["s1"], 3: ["s1"], 4: ["s2"]}, must_miss={5: ["s1", "s2"], 7: ["s1", "s2"]},
         authored=True,
         notes="Picture 1 of #348. Inside A's call, T1 asks V1, then asks V2 with what V1 returned, then answers A. "
               "T1's second request inherits from the call that opened it (s1 2>5) and from V1's answer inside the "
               "call (s2 4>5); its answer carries both (2>7, 4>7). Before #348 the fold drew s2 4>5 only, and no 4>7.")

    p2 = ("U>A1[s1 -], U>A2[s2 -], A1>T1[s1 -], A2>T1[s2 -], T1(A1)>V1[s1@subject -]⚑, V1>T1[-], "
          "T1(A2)>V2[s2@account -], V2>T1[-], T1>A1[s1@subject -], T1>A2[s2@account -], A1>U[-], A2>U[-]")
    case("P2", "two callers in flight", p2, cell="trunk/control/two-callers-in-flight", verdict=ba,
         edges=["s1 1>3", "s1 3>5", "s1 3>9", "s2 2>4", "s2 4>7", "s2 4>10"],
         must_see={1: ["s1"], 2: ["s2"], 3: ["s1"], 4: ["s2"]},
         must_miss={5: ["s1"], 7: ["s2"], 9: ["s1"], 10: ["s2"]}, authored=True,
         notes="Picture 2 of #348. A1 and A2 call T1; A2's request arrives before T1 serves A1. T1(A1)>V1 belongs to "
               "A1's call, so it carries s1 (3>5) and nothing of A2's; before #348 the fold routed it from the latest "
               "inbound (A2's) and V1 saw no label: verdict allow.")

    p3 = ("U>A1[s1 -], U>A2[s1 -], A1>T1[s1 -], A2>T1[s1 -], T1(A1)>V1[s1@subject -]⚑, V1>T1[-], "
          "T1(A2)>V2[s1@subject -], V2>T1[-], T1>A1[s1@subject -], T1>A2[s1@subject -], A1>U[-], A2>U[-]")
    case("P3", "two callers, one value", p3, cell="trunk/control/two-callers-one-value", verdict=ba,
         edges=["s1 1>3", "s1 2>4", "s1 3>5", "s1 4>7", "s1 3>9", "s1 4>10"],
         must_see={1: ["s1"], 2: ["s1"], 3: ["s1"], 4: ["s1"]}, must_miss={5: ["s1"], 7: ["s1"], 9: ["s1"], 10: ["s1"]},
         authored=True,
         notes="Picture 2 of #348 with the same value in both calls, so a wrong parent shows as an edge that should not "
               "exist: T1(A1)>V1 must inherit from A1's request (3>5), not from A2's, which arrived later (4>5). "
               "Before #348 the fold drew 4>5, and 3>5 and 4>7 were missing.")

    across_cases()


def across_cases() -> None:
    """K8–K30. Every case is K0's two conversations, or more of them, unless it says otherwise."""
    aa = v("allow", "allow", "allow")
    crossed_block = v("allow", "allow", "block")
    all4 = ["s1", "s2", "s3", "s4"]

    def two(id_, title, sent, back, *, cell, notes, level=None, crossed=(), units=None, keys=None, gap="~10min>",
            **kw):
        """Conversation 1 sends *sent* to V2; V2 returns *back* in conversation 2. *crossed* lists the returned
        units expected to cross at *level*; nothing crosses when it is empty."""
        line = f"{sends(sent, keys)} {gap} {returns(back)}"
        cross = crossing(11, 22, crossed, level) + carried(16, crossed) if crossed else []
        evidence = {**received(11, back), 22: {u: [f"crossed {level}"] if u in crossed else [] for u in back},
                    **every(range(23, 28), crossed, f"crossed {level}")}
        kw.setdefault("verdict", crossed_block if crossed else aa)
        case(id_, title, line, cell=cell, units=units or UNITS, edges=sent_edges(0, sent), cross=cross,
             evidence=kw.pop("evidence", evidence), must_see=sent_premise(0, sent),
             must_miss=returned_premise(16, back), authored=True, notes=notes, **kw)

    # --- the rule ---------------------------------------------------------------------------------------------
    two("K8", "no identifier among the returned values", all4, ["s3", "s4"],
        cell="across/rule/no-anchor",
        notes="Conversation 1 sends the whole record (SSN, MRN, birthdate, diagnosis) to V2; V2 returns the birthdate "
              "and the diagnosis. Two plain values of one record corroborate each other, but neither is an anchoring "
              "identifier: no crossing, allow (twins and households share exactly such pairs). Both "
              "labels are PI, so T-SENS would allow even if they crossed: the edges carry the claim. "
              + TRIVIAL.format("the anchor rule off"))

    two("K9", "a name alone", ["s1", "s5"], ["s5"], units={**UNITS, "s5": "PN"},
        cell="across/rule/no-anchor",
        notes="Conversation 1 sends the SSN and the patient's name; V2 returns the name alone. A person's name does "
              "not anchor: no crossing, allow. The name is PII, so a wrong crossing would block. "
              + TRIVIAL.format("the anchor rule off"))

    # K10: two earlier conversations each send V2 the same identifier; the third conversation gets it back.
    k10 = f"{sends(THREE)} ~2min> {sends(THREE)} ~2min> {returns(['s1'])}"
    k10_cross = (crossing(11, 38, ["s1"], "vo") + crossing(27, 38, ["s1"], "vo") + carried(32, ["s1"]))
    case("K10", "one identifier from two earlier conversations", k10, cell="across/rule/ambiguous",
         verdict=crossed_block, edges=sent_edges(0, THREE) + sent_edges(16, THREE), cross=k10_cross,
         evidence={**received(11, ["s1"]), **received(27, ["s1"]),
                   **every(range(38, 44), ["s1"], "crossed vo ambiguous")},
         must_see={**sent_premise(0, THREE), **sent_premise(16, THREE)}, must_miss=returned_premise(32, ["s1"]),
         authored=True,
         notes="Conversations 1 and 2 each send V2 the SSN (with the MRN and the birthdate); conversation 3 gets the SSN "
               "alone back. Identity is the origin, never the text: two edges, each value-only, and the result is "
               "ambiguous. It blocks (R5): what is uncertain is which conversation it came from. "
               "The ambiguity is stated on every later leg too: the policy reads it at the flagged leg.")

    # K11a/b: event times are exact: leg 11 leaves at 09:00:16; conversation 2's partner answer (leg 22) at 38 s
    # plus the gap plus 0.1 s after 09:00:00. The retention of a time-based entity is 15 minutes (R9) and the
    # interval is open (R1): valid while 09:00:16 < t < 09:15:16.
    two("K11a", "back one step inside the retention", THREE, ["s1"], gap="~876s>", level="vo", crossed=["s1"],
        cell="across/rule/interval",
        notes="K2 with conversation 2 starting 876 s after conversation 1: V2 answers at 09:15:14.1, 1.9 s before "
              "the occurrence of leg 11 expires (09:15:16, R1, R9). The crossing stands, value-only, and blocks.")
    two("K11b", "back one step after the retention", THREE, ["s1"], gap="~896s>",
        cell="across/rule/interval",
        notes="K2 with conversation 2 starting 896 s after conversation 1: V2 answers at 09:15:34.1, 18.1 s after the "
              "occurrence of leg 11 expired (09:15:16, R1, R9): no crossing, allow. "
              + TRIVIAL.format("the valid interval ignored"))

    # K11c: one conversation 2 across the expiry. V2 answers T2's first call inside the retention and its third call,
    # inside the same call of T2, after it. The occurrence is valid inside conversation 2's window, so it is loaded;
    # only the interval at each leg keeps the later answer from crossing (R1).
    k11c_back = ("U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[s1@subject -], T2>V2[-], V2>T2[-], T2>V2[-], "
                 "V2>T2[s1@subject -], T2>A[s1@subject -], A>L[s1@subject -], L>A[s1@subject -], A>T3[s1@subject -], "
                 "T3>V3[s1@subject -]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]")
    k11c_cross = crossing(11, 22, ["s1"], "vo") + chain([22, 27, 28, 29, 30, 31], ["s1"]) + held_earlier(27, 30, ["s1"])
    case("K11c", "one conversation across the expiry", f"{sends(THREE)} ~874s> {k11c_back}", cell="across/rule/interval",
         verdict=crossed_block, edges=sent_edges(0, THREE), cross=k11c_cross,
         evidence={**received(11, ["s1"]), 22: {"s1": ["crossed vo"]}, 26: {"s1": []},
                   **every(range(27, 32), ["s1"], "crossed vo")},
         must_see=sent_premise(0, THREE), must_miss={i: ["s1"] for i in (22, 26, 27, 28, 29, 30, 31)}, authored=True,
         notes="K2 with conversation 2 starting 874 s after conversation 1 and V2 answering twice: at 09:15:12.1, "
               "3.9 s before the occurrence of leg 11 expires (09:15:16, R1, R9), it returns the SSN and the crossing "
               "stands (22); at 09:15:20.1, 4.1 s after, it returns it again and nothing crosses (26). The occurrence "
               "is loaded for conversation 2 (it is valid at leg 22), so only the interval checked at each leg tells "
               "the two answers apart. " + TRIVIAL.format("the valid interval ignored at the leg"))

    two("K12", "the partner declared call scope", THREE, THREE, scopes={"V2": "call"},
        cell="across/rule/scope",
        notes="K1 with V2 declared call scope: it remembers nothing past one call, so nothing it returns in "
              "conversation 2 can cross. No crossing, allow. A declared key that is no derived entity makes the run "
              "red; that the declaration reaches the processor at all is K22c's check. "
              + TRIVIAL.format("the declaration ignored, V2 back at its default, persistent"))

    # K13: a persistent agent; user B's conversation comes first in time, user A's later, but A's legs are sent first
    # so A's occurrence is in the table when B's leaving leg is matched: only the interval keeps them apart (R1).
    k13 = ("U>A[-], A>L[-], L>A[s1@subject -], A>T2[s1@subject -], T2>V2[s1@subject -]⚑, V2>T2[-], T2>A[-], A>U[-] "
           "~1min> U>A[s1 -], A>U[-]")
    case("K13", "a persistent agent, the value arrives after it left", k13, cell="across/rule/interval",
         scopes={"A": "persistent"},
         steps=[{"send": "9-10"}, "drain", {"send": "1-8"}, "drain"],
         verdict=aa, edges=[], evidence={4: {"s1": []}, 9: {"s1": ["classified SSN"]}},
         must_see={9: ["s1"]}, must_miss={3: ["s1"], 4: ["s1"], 5: ["s1"]}, authored=True,
         notes="Agent A is declared persistent and serves two users. In user B's conversation (legs 1-8) the LLM "
               "writes a number equal to user A's SSN, unlabelled, and A sends it on at 09:00:06. User A gives A that "
               "SSN at 09:01:16 (leg 9). A's legs arrive first, so the occurrence exists when B's leg is matched, "
               "but it is valid only after 09:01:16 (R1): no crossing, allow. "
               + TRIVIAL.format("the valid interval ignored"))

    # K14: a listing of two patients, each a record; V2 returns patient A's SSN and patient B's birthdate.
    k14_units = {"s1": "SSN", "s3": "DOB", "s5": "SSN", "s6": "DOB"}
    listing = "{s1 s3} {s5 s6}"
    k14 = (f"U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[{listing} -], A>L[{listing} -], L>A[{listing} -], "
           f"A>T2[{listing} -], T2>V2[{listing} -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-] ~10min> "
           + returns(["s1", "s6"]))
    four = ["s1", "s3", "s5", "s6"]
    k14_cross = crossing(11, 22, ["s1"], "vo") + carried(16, ["s1"])
    case("K14", "two patients, two records", k14, cell="across/rule/same-record", units=k14_units,
         verdict=crossed_block, edges=sent_edges(0, four), cross=k14_cross,
         evidence={**received(11, four, k14_units), 22: {"s1": ["crossed vo"], "s6": []},
                   **every(range(23, 28), ["s1"], "crossed vo")},
         must_see=sent_premise(0, four), must_miss=returned_premise(16, ["s1", "s6"]), authored=True,
         notes="Conversation 1 sends V2 a listing of two patients, one record each: {SSN A, birthdate A} and "
               "{SSN B, birthdate B}. V2 returns A's SSN and B's birthdate. They left in different records, so they "
               "do not corroborate (R4): A's SSN crosses value-only, B's birthdate has no anchor in its record and "
               "draws nothing. Never corroborated; it blocks on the SSN (R5).")

    case("K15", "the record number read as an operational identifier",
         f"{sends(THREE, {'s2': 's2@external_id'})} ~10min> {returns(['s2'])}", cell="across/rule/label-type",
         units={**UNITS, "s2": ["MRN", "OPID"]}, verdict=aa, edges=sent_edges(0, ["s1", "s3"]),
         evidence={11: {"s2": ["classified OPID"]}, 22: {"s2": []}},
         must_see={**sent_premise(0, ["s1", "s3"]), 11: THREE},
         must_miss={**{i: ["s2"] for i in (7, 8, 9, 10)}, **returned_premise(16, ["s2"])}, authored=True,
         notes="Conversation 1 sends the MRN under external_id; at leg 11, where V2 receives it, the model labels it "
               "an operational identifier (OPID), not a person identifier. Whether a value anchors follows the type it "
               "was received with (R8): no crossing, allow. The model's findings on the MRN at legs 7-10 are "
               "subtracted (authored): it reads it as OPID there too, except at leg 8 (DLN), which would give the value "
               "a second origin of another type. " + TRIVIAL.format("the anchor rule off"))

    # K16: origin a sent the SSN and the birthdate in one record, origin b the SSN alone; both come back.
    k16_units = {"s1": "SSN", "s3": "DOB"}
    k16 = f"{sends(['s1', 's3'])} ~2min> {sends(['s1'])} ~2min> {returns(['s1', 's3'])}"
    k16_cross = (crossing(11, 38, ["s1", "s3"], "co") + crossing(27, 38, ["s1"], "vo")
                 + carried(32, ["s1", "s3"]))
    case("K16", "one identifier, two origins, two levels", k16, cell="across/rule/ambiguous", units=k16_units,
         verdict=crossed_block, edges=sent_edges(0, ["s1", "s3"]) + sent_edges(16, ["s1"]), cross=k16_cross,
         evidence={**received(11, ["s1", "s3"]), **received(27, ["s1"]),
                   **{i: {"s1": ["crossed co ambiguous"], "s3": ["crossed co"]} for i in range(38, 44)}},
         must_see={**sent_premise(0, ["s1", "s3"]), **sent_premise(16, ["s1"])},
         must_miss=returned_premise(32, ["s1", "s3"]), authored=True,
         notes="Conversation 1 sends V2 the SSN with the birthdate, conversation 2 the SSN alone; conversation 3 gets "
               "both back. Origin 1 is corroborated, origin 2 value-only: the SSN carries the strongest, "
               "corroborated, and is ambiguous (R3); the birthdate has one origin.")

    # --- the level on its way ---------------------------------------------------------------------------------
    k17 = (f"{sends(THREE)} ~2min> {returns(THREE, flag=False)} ~2min> "
           + returns(["s1"], tool="T3", partner="V3", on=("T4", "V4")))
    k17_cross = (crossing(11, 22, THREE, "co") + carried(16, THREE) + crossing(27, 38, ["s1"], "vo")
                 + carried(32, ["s1"]))
    case("K17", "a relay through two crossings", k17, cell="across/level/relay",
         verdict=crossed_block, edges=sent_edges(0, THREE), cross=k17_cross,
         evidence={**received(11, THREE), **every(range(22, 28), THREE, "crossed co"),
                   **every(range(38, 44), ["s1"], "crossed vo")},
         must_see=sent_premise(0, THREE), must_miss={**returned_premise(16, THREE), **returned_premise(32, ["s1"])},
         authored=True,
         notes="Conversation 1 to 2 by a corroborated crossing at V2 (as K1): V3 receives the three at leg 27 as "
               "crossed, corroborated, and holds them (what is registered includes a crossing's label). "
               "Conversation 3 gets the SSN alone back from V3: value-only. Along the path the weakest level "
               "holds: the label reaches conversation 3 as crossed, value-only.")

    k18 = f"{sends(THREE)} ~10min> " + returns(THREE, answer=f"{SEEN} -")
    k18_cross = crossing(11, 22, THREE, "co")
    case("K18", "classified and crossed", k18, cell="across/level/both",
         verdict=v("allow", "block", "block"), edges=sent_edges(0, THREE) + carried(16, THREE), cross=k18_cross,
         evidence={**received(11, THREE), 22: {u: [f"classified {TYPE[u]}", "crossed co"] for u in THREE},
                   **every(range(23, 28), THREE, "inherited", "crossed co")},
         must_see={**sent_premise(0, THREE), 22: THREE}, must_miss={i: THREE for i in range(23, 28)},
         authored=True,
         notes="K1 with V2's answer under the keys the model recognises: the classifier labels the three at leg 22 "
               "too. The leg shows both, classified and crossed, corroborated; neither changes the other. "
               "On every later leg both reach it: inherited (the label classified at 22) and crossed. The "
               "conversation's own edges from 22 are drawn in every mode, so conv already blocks.")

    # --- a value held from many conversations -------------------------------------------------------------------------------------
    k19 = " ~2min> ".join([to_partner()] * 3 + [back_from_partner()])
    k19_cross = (crossing(3, 22, ["s1"], "vo") + crossing(9, 22, ["s1"], "vo") + crossing(15, 22, ["s1"], "vo")
                 + chain([22, 23, 24, 25], ["s1"]))
    case("K19", "three earlier conversations", k19, cell="across/many/three", units={"s1": "SSN"},
         verdict=crossed_block, edges=[e for b in (0, 6, 12) for e in chain([b + 1, b + 2, b + 3], ["s1"])],
         cross=k19_cross,
         evidence={3: {"s1": ["classified SSN", "inherited"]}, 9: {"s1": ["classified SSN", "inherited"]},
                   15: {"s1": ["classified SSN", "inherited"]},
                   **every(range(22, 26), ["s1"], "crossed vo ambiguous")},
         must_see={b + i: ["s1"] for b in (0, 6, 12) for i in (1, 2, 3)}, must_miss={i: ["s1"] for i in range(22, 26)},
         authored=True,
         notes="Three earlier conversations each send V2 the user's SSN; the fourth gets it back: three edges, one "
               "per conversation it is held from, value-only, ambiguous (three origins, R3); it blocks.")

    k20 = " ~2min> ".join([to_partner(3)] + [to_partner()] * 3 + [back_from_partner()])
    k20_edges = (chain([1, 2, 3], ["s1"]) + ["s1 2>5", "s1 2>7"]
                 + [e for b in (10, 16, 22) for e in chain([b + 1, b + 2, b + 3], ["s1"])])
    k20_premise = dict(
        must_see={**{i: ["s1"] for i in (1, 2, 3, 5, 7)}, **{b + i: ["s1"] for b in (10, 16, 22) for i in (1, 2, 3)}},
        must_miss={i: ["s1"] for i in range(32, 36)}, authored=True)
    k20_on = chain([32, 33, 34, 35], ["s1"])
    case("K20", "four earlier conversations, one of them three times", k20, cell="across/many/four",
         units={"s1": "SSN"}, verdict=crossed_block, edges=k20_edges,
         cross=[e for frm in (3, 5, 7, 13, 19, 25) for e in crossing(frm, 32, ["s1"], "vo")] + k20_on,
         evidence=every(range(32, 36), ["s1"], "crossed vo ambiguous"), **k20_premise,
         notes="Four earlier conversations send V2 the SSN, the first of them three times (three calls in one call of "
               "T2); the fifth gets it back. No value is dropped for being held from many conversations (R2): six "
               "edges, one per time V2 received it, value-only, ambiguous (four origins); it blocks. Under the cap "
               "of 30 edges per value, so nothing is cut and no mark is drawn.")
    case("K20b", "the same, with at most two edges per value", k20, cell="across/many/capped",
         units={"s1": "SSN"}, config={"edges_per_value": 2}, verdict=crossed_block, edges=k20_edges,
         cross=crossing(19, 32, ["s1"], "vo") + crossing(25, 32, ["s1"], "vo") + k20_on,
         evidence=every(range(32, 36), ["s1"], "crossed vo ambiguous"), marks={32: ["capped 4"]}, **k20_premise,
         notes="K20 with the cap set to two. Predicted from the rule before the run: of the six times V2 received "
               "the SSN the two newest are legs 25 and 19, so two edges are drawn, from two origins: still "
               "ambiguous, still a block. The mark says the value was cut and counts the conversations it is held "
               "from, 4; counted by leg it would be 6.")

    # --- scope ------------------------------------------------------------------------------------------------
    k21 = ("U>A[-], A>T2[-], T2>V1[-], V1>T2[s1 -], T2>V2[s1 -], V2>T2[s1@subject -], T2>A[-], A>T3[-], T3>V2[-], "
           "V2>T3[s1@subject -], T3>A[s1@subject -], A>T4[s1@subject -], T4>V4[s1@subject -]⚑, V4>T4[-], T4>A[-], "
           "A>U[-]")
    k21_cross = ["s1 5>10 vo", "s1 10>11", "s1 11>12", "s1 12>13"]
    case("K21", "the partner returns it in a later call", k21, cell="across/scope/same-conversation",
         units={"s1": "SSN"}, verdict=crossed_block, edges=["s1 4>5", "s1 5>6"], cross=k21_cross,
         evidence={5: {"s1": ["classified SSN", "inherited"]}, 6: {"s1": ["inherited"]},
                   **every(range(10, 14), ["s1"], "crossed vo")},
         must_see={4: ["s1"], 5: ["s1"]}, must_miss={i: ["s1"] for i in (6, 10, 11, 12, 13)}, authored=True,
         notes="One conversation. T2 gets the SSN from V1 and sends it to V2, which echoes it inside the same call (5>6, "
               "the conversation's own edge). In a later call, from T3, V2 returns it again. V2 is persistent: the "
               "same mechanism links the two calls (R7), one edge, value-only; inside one call the existing edge "
               "stands alone, never a second row (5>6 is not drawn again as a crossing).")

    k22 = ("U>A[-], A>T2[-], T2>V1[-], V1>T2[s1 s2 -], T2>A[-], A>T2[-], T2>A[s1@subject s2@account -], "
           "A>T3[s1@subject s2@account -], T3>V3[s1@subject s2@account -]⚑, V3>T3[-], T3>A[-], A>U[-]")
    k22_units = {"s1": "SSN", "s2": "MRN"}
    k22_cross = crossing(4, 7, ["s1", "s2"], "co") + chain([7, 8, 9], ["s1", "s2"])
    k22_premise = dict(must_see={4: ["s1", "s2"]}, must_miss={i: ["s1", "s2"] for i in (7, 8, 9)}, authored=True)
    case("K22a", "a tool declared a store", k22, cell="across/scope/declared-store", units=k22_units,
         scopes={"T2": "persistent"}, verdict=crossed_block, edges=[], cross=k22_cross,
         evidence={4: {"s1": ["classified SSN"], "s2": ["classified MRN"]},
                   **every((7, 8, 9), ["s1", "s2"], "crossed co")}, **k22_premise,
         notes="T2 is declared persistent (a store). In its first call it fetches the SSN and the MRN from V1 (it "
               "received them); in a second call it returns them. One edge per anchoring unit, corroborated (two "
               "identifiers of one record), carried to V3, which blocks.")
    case("K22b", "the same tool, not declared", k22, cell="across/scope/declared-store", units=k22_units,
         verdict=aa, edges=[], evidence={7: {"s1": [], "s2": []}}, **k22_premise,
         notes="K22a without the declaration: a tool is call scope by default and keeps nothing between its two "
               "calls. No edge, allow. " + TRIVIAL.format("a tool persistent by default"))
    case("K22c", "the same tool, declared conversation", k22, cell="across/scope/declared-store", units=k22_units,
         scopes={"T2": "conversation"}, verdict=v("allow", "block", "block"),
         edges=chain([4, 7, 8, 9], ["s1", "s2"]),
         evidence={4: {"s1": ["classified SSN"], "s2": ["classified MRN"]},
                   **every((7, 8, 9), ["s1", "s2"], "inherited")}, **k22_premise,
         notes="K22b with T2 declared conversation scope: inside one conversation it pools its two calls, so what it "
               "received in the first (leg 4) reaches what it returns in the second (leg 7), an edge of the "
               "conversation's own, carried to V3, which blocks once inherited labels are on. The declaration is what "
               "draws the edges: if it did not reach the processor, the line would be K22b's.")

    # --- limits, order, correction ----------------------------------------------------------------------------
    # A limit is applied where a persistent entity (V2, V3) sends a payload, and marks that leg whenever it is
    # exceeded there, whether or not the cut hid anything (the cut set asserts nothing).
    k1_line = f"{sends(THREE)} ~10min> {returns(THREE)}"
    no_cross = {**received(11, THREE), 22: {u: [] for u in THREE}}
    case("K23a", "a limit fires: holdings per entity", k1_line, cell="across/limits/holdings",
         config={"holdings_per_entity": 2}, verdict=aa, edges=sent_edges(0, THREE), evidence=no_cross,
         marks={12: ["incomplete holdings_per_entity"], 22: ["incomplete holdings_per_entity"]},
         must_see=sent_premise(0, THREE), must_miss=returned_premise(16, THREE), authored=True,
         notes="K1 with each entity searched only while it holds at most two occurrences. Predicted from the rule "
               "before the run: V2 receives the three values at leg 11, so at its own answer (leg 12) it holds "
               "three, and at leg 22, ten minutes later and inside the retention, it still holds conversation 1's "
               "three: both legs are marked incomplete, nothing is searched there, nothing crosses, allow (K1 "
               "blocks). V3 receives nothing labelled at leg 27, so its answer (leg 28) is not cut.")
    two("K23b", "a limit fires: payload bytes", THREE, ["s1"], config={"payload_bytes": 16},
        marks={i: ["incomplete payload_bytes"] for i in (12, 22, 28)}, cell="across/limits/payload-bytes",
        notes="K2 with at most 16 bytes of a payload searched. Predicted from the rule before the run: every payload "
              "a persistent entity sends is longer than 16 bytes (V2's at legs 12 and 22, 43 and 50 bytes; V3's at "
              "leg 28, 24 bytes), so all three are marked; at leg 22 the SSN starts after byte 16, so it is not "
              "found and nothing crosses: allow.")
    k23c = f"{sends(THREE)} ~2min> {sends(THREE)} ~2min> {returns(['s1'])}"
    case("K23c", "a limit fires: candidates per payload", k23c, cell="across/limits/candidates",
         config={"candidates_per_payload": 1}, verdict=aa, edges=sent_edges(0, THREE) + sent_edges(16, THREE),
         evidence={38: {"s1": []}}, marks={38: ["incomplete candidates_per_payload"]},
         must_see={**sent_premise(0, THREE), **sent_premise(16, THREE)}, must_miss=returned_premise(32, ["s1"]),
         authored=True,
         notes="K10 with one candidate per payload. Predicted from the rule before the run: the SSN at leg 38 has "
               "two candidates (conversations 1 and 2 each sent it to V2), one more than the limit, so nothing is "
               "asserted there and the leg is marked; V2's answers at legs 12 and 28 and V3's at 44 find no "
               "held value at all. No crossing, allow.")

    k1_cross = crossing(11, 22, THREE, "co") + carried(16, THREE)
    k1 = dict(cell="across/order", verdict=crossed_block, edges=sent_edges(0, THREE), cross=k1_cross,
              evidence={**received(11, THREE), **every(range(22, 28), THREE, "crossed co")},
              must_see=sent_premise(0, THREE), must_miss=returned_premise(16, THREE), authored=True)
    case("K24", "conversation 1 arrives after conversation 2 was processed", k1_line,
         steps=[{"send": "17-32"}, "drain", {"send": "1-16"}, "drain"],
         **{**k1, "verdict": v("allow", "allow", ["allow", "block late"])},
         notes="K1 with conversation 2 sent and drained before conversation 1 is sent: conversation 1 is classified "
               "and derived after conversation 2 was matched (the readiness cursor derives a leg only once it is "
               "classified, so a late classification is a late leg). Conversation 1's occurrences at V2 queue "
               "conversation 2, whose V2 answer lies inside their interval, and it is derived again: the same final "
               "edges as in order. The decision history, every version, predicted from the mechanism before the "
               "run: a version is written only when the computed record differs. At the first drain both legs of "
               "the flagged exchange are on the wire when leg-ready delivers its request, so one record evidences "
               "both (v1 allow; the response leg computes the same record, no version). At the second drain the "
               "correction changes the flagged leg's labels and queues its recompute: v2 block, stricter than v1, "
               "which evidenced the same legs: a late finding. The cross run's "
               "history is [allow, block late]. That conversation 2 is derived exactly once more is the driver "
               "test's (test_a_late_conversation_corrects_the_one_derived_before_it_once).")
    k25_edges = sent_edges(0, ["s2", "s3"])
    case("K25", "the identifier's label removed from conversation 1", f"{sends(THREE)} ~10min> {returns(['s1'])}",
         cell="across/order", steps=[{"send": "1-32"}, "drain", {"reclassify": {i: ["s1"] for i in range(7, 12)}},
                                     {"derive": 1}, "drain", {"derive": 1}, "drain"],
         verdict=v("allow", "allow", ["block", "allow"]), edges=k25_edges, evidence={22: {"s1": []}, 27: {"s1": []}},
         must_see=sent_premise(0, THREE), must_miss=returned_premise(16, ["s1"]), authored=True,
         notes="K2, then every payload is classified again by ADR-0024's runbook, the model now reading legs 7-11 "
               "without the SSN, and conversation 1 is queued and derived again (the runbook re-derives nothing by "
               "itself). Its SSN occurrence at V2 is gone, which queues conversation 2: it loses the crossing and "
               "every label that rested on it, and the flagged decision gets one new version (block, then allow). "
               "The expected rows are the final state, with no SSN edge in conversation 1 either. Conversation 1 is "
               "derived once more: it changes no occurrence, queues nothing, and writes no version. The history, "
               "every version, predicted from the mechanism before the run: v1 block at the first drain (one record "
               "evidences both legs of the flagged exchange), v2 allow at the second (the correction's recompute), "
               "nothing at the third (no summary changed, no recompute): [block, allow]. "
               "The classifier premise states the first classification; the rows are checked against it with the "
               "reclassified cells unlabelled.")
    case("K26", "every leg arrives reversed", k1_line, steps=[{"send": "32-1"}, "drain"], **k1,
         notes="K1 with every leg of both conversations sent in reverse order, then drained: the same final edges.")
    case("K27", "conversation 2 derived twice", k1_line, steps=[{"send": "1-32"}, "drain", {"derive": 2}, "drain"],
         **k1,
         notes="K1, then conversation 2 is queued for derivation again (the production correction queue) and drained: "
               "identical rows, each edge one row; the occurrences it registered are replaced, not doubled.")

    # --- switches and tiers -----------------------------------------------------------------------------------
    case("K28c", "cross-conversation off for conversation 1", k1_line,
         steps=[{"cross": "off"}, {"send": "1-16"}, "drain", {"cross": "on"}, {"send": "17-32"}, "drain"],
         cell="across/switch", verdict=aa, edges=sent_edges(0, THREE), evidence=every([22, 27], THREE),
         must_see=sent_premise(0, THREE), must_miss=returned_premise(16, THREE), authored=True,
         notes="K1 with the cross-conversation switch off while conversation 1 runs and on for conversation 2. "
               "Nothing was registered while it was off, and switching on does not look back (R6): no crossing, "
               "allow. K28a (inherited labels off) and K28b (cross-conversation off) are not lines of their own: "
               "every case's off and conv runs are those two, and K1-K7 are where they bite.")
    case("K29", "the classifier alone blocks", legs({1: "s1 s2 s3 s4", 2: SEEN, 3: SEEN, 4: SEEN, 5: SEEN}, flag=5),
         cell="across/tiers", verdict=v(["block tier 0"], ["block tier 0"], ["block tier 0"]),
         edges=chain([1, 2, 3, 4, 5]) + held_earlier(1, 4),
         must_see={1: all4, 2: THREE, 3: THREE, 4: THREE, 5: THREE}, must_miss={},
         notes="C2: the classifier labels the three on the flagged leg, so tier 0 (classification alone) blocks. "
               "The history, every version, predicted from the mechanism before the run: one version in every mode "
               "(both legs of the flagged exchange are on the wire when leg-ready delivers its request; the response "
               "computes the same record). The policy is asked per tier on that snapshot (off: tier 0 only; conv: "
               "0 and 1, the chain's labels reaching leg 5; cross: 0, 1 and 2, tier 2's input equal to tier 1's), "
               "every tier blocks, and a block is decided at the first tier that blocks: [block tier 0] in every "
               "mode. That the answer comes before any lineage is asked is timing, which this harness does not "
               "show: the record says the classifier alone decided it.")
    case("K29b", "the classifier's block is released before label lineage",
         legs({1: "s1 s2 s3 s4", 2: SEEN, 3: SEEN, 4: SEEN, 5: SEEN}, flag=5),
         cell="across/tiers/early", steps=[{"send": "1-16"}, "ask", "drain"],
         verdict=v(["block tier 0"], ["block tier 0", "block tier 0"], ["block tier 0", "block tier 0"]),
         edges=chain([1, 2, 3, 4, 5]) + held_earlier(1, 4),
         must_see={1: all4, 2: THREE, 3: THREE, 4: THREE, 5: THREE}, must_miss={},
         notes="K29 with a gate asking while label lineage is held back (the ask step). A decision is released when "
               "it is a block, or when the evidence is complete. Predicted from the mechanism before the run. conv and "
               "cross: at the ask the flagged request is classified and label lineage has not derived it, so the "
               "policy is asked on the classification alone; it blocks, and version 1 is written there, tier 0, with "
               "nothing inherited in it. The drain then computes the full evidence: the chain's labels reach leg 5, "
               "the record differs, version 2, a block again and so not a late finding. off: a classified leg is a "
               "ready leg, the ask computes the full record and the drain writes nothing more: one version. Red when "
               "the early question never fires (one version in conv and cross).")
    k30b = f"{sends(THREE)} ~10min> {returns(['s1'])}"
    case("K30b", "only the crossing blocks: nothing is released before label lineage", k30b,
         cell="across/tiers/early", steps=[{"send": "1-16"}, "drain", {"send": "17-32"}, "ask", "drain"],
         verdict=v(["allow tier 0"], ["allow tier 1"], ["block tier 2"]),
         edges=sent_edges(0, THREE), cross=crossing(11, 22, ["s1"], "vo") + carried(16, ["s1"]),
         evidence={**received(11, ["s1"]), **every(range(22, 28), ["s1"], "crossed vo")},
         must_see=sent_premise(0, THREE), must_miss=returned_premise(16, ["s1"]), authored=True,
         notes="K2 with a gate asking for the flagged exchange of conversation 2 while label lineage is held back "
               "(the ask step). Predicted from the mechanism before the run. conv and cross: the flagged request is "
               "classified and nothing in conversation 2 is labelled, so the early question does not block: nothing "
               "is written and the gate is not answered. The drain then decides on the full evidence, once: cross, "
               "block at tier 2 from the crossed SSN, the first version and so not late; conv, allow at tier 1. off: "
               "a classified leg is a ready leg, the ask computes allow at tier 0. Red when the early question "
               "writes what is not a block (cross would read [allow tier 0, block tier 2 late]).")
    case("K30", "the cross tier comes after the answer", f"{sends(THREE)} ~10min> {returns(['s1'])}",
         cell="across/tiers", steps=[{"cross": "off"}, {"send": "1-32"}, "drain", {"cross": "on"}, {"derive": 1},
                                     {"derive": 2}, "drain"],
         verdict=v(["allow tier 0"], ["allow tier 1"], ["allow tier 1", "block tier 2 late"]),
         edges=sent_edges(0, THREE), cross=crossing(11, 22, ["s1"], "vo") + carried(16, ["s1"]),
         evidence={**received(11, ["s1"]), **every(range(22, 28), ["s1"], "crossed vo")},
         must_see=sent_premise(0, THREE), must_miss=returned_premise(16, ["s1"]), authored=True,
         notes="K2 with tier 2 (the crossed labels) absent at the first decision because the cross-conversation "
               "switch was off, not because it was slow: the timing of a gate's limit is a live run's, which "
               "this harness cannot show. Both conversations are sent and drained with the switch off; it is turned "
               "on (as a restart would) and both are derived again: switching on does not look back (R6), so "
               "conversation 1 registers its occurrences only when derived again, and conversation 2, derived while "
               "the switch was off, wrote no send rows a correction could find, so it is queued too. The crossing "
               "is drawn and the flagged decision recomputed. The "
               "history, every version, predicted from the mechanism before the run. cross: v1 allow at tier 1 "
               "(tiers 0 and 1 were on, nothing in conversation 2 is labelled), v2 block at tier 2 (the crossed SSN), "
               "stricter than v1: a late finding; v1 stands, the finding does not reach back. conv: the switch stays "
               "off whatever a step says, so tiers 0 and 1 only: one version, [allow tier 1]. off: inherited labels "
               "off, tier 0 only; the crossing is drawn (the switch governs label lineage) but reaches no decision: "
               "one version, [allow tier 0].")


if __name__ == "__main__":
    main()
