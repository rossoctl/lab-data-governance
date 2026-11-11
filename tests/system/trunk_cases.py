"""Write the trunk cases (C1–C6, K0–K7) and the #348 controls (P1–P3) as case.yaml files. Run once; the files
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
PENDING = ("cross-conversation lineage not on this branch: the cross-trace fold (#273) with its seed rule and window, "
           "and the risk engine forwarding a cross-trace origin (labels.forwarded keeps same-trace origins only)")


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
    """The edges from an earlier holding the agent remembers, beside the expected chain (``also``)."""
    return [f"{u} {at}>{to}" for u in units]


def case(id_, title, line, *, verdict, edges, must_see, must_miss, notes, cell, pending=None, authored=False,
         also=()) -> None:
    d = {
        "id": id_, "title": title, "cell": cell, "line": line, "units": UNITS,
        "expect": {"verdict": verdict, "edges": edges, "also": list(also), "cannot": None, "pending": pending},
        "classifier": {"must_see": {str(k): v for k, v in must_see.items()},
                       "must_miss": {str(k): v for k, v in must_miss.items()}, "authored": authored},
        "notes": notes,
    }
    p = CASES_DIR / id_
    p.mkdir(exist_ok=True)
    (p / "case.yaml").write_text(yaml.dump(d, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=120))


def main() -> None:
    all4 = ["s1", "s2", "s3", "s4"]
    bb, ba = {"on": "block", "off": "block"}, {"on": "block", "off": "allow"}

    case("C1", "quiet", legs({}, flag=5), cell="trunk/one/quiet",
         verdict={"on": "allow", "off": "allow"}, edges=[], must_see={}, must_miss={},
         notes="No route. Nothing sensitive is on the wire; the control for the rule and the harness.")

    case("C2", "seen throughout", legs({1: "s1 s2 s3 s4", 2: SEEN, 3: SEEN, 4: SEEN, 5: SEEN}, flag=5),
         cell="trunk/one/root-to-left-leaf", verdict=bb, edges=chain([1, 2, 3, 4, 5]), also=held_earlier(1, 4),
         must_see={1: all4, 2: THREE, 3: THREE, 4: THREE, 5: THREE}, must_miss={},
         notes="Root to the left leaf, U>A>T1>V1. The classifier labels the three on every leg; lineage draws the chain, "
               "the classifier alone already decides.")

    case("C3", "lost after the prompt", legs({1: "s1 s2 s3 s4", 2: SEEN, 3: MISS, 4: MISS, 5: MISS}, flag=5),
         cell="trunk/one/root-to-left-leaf", verdict=ba, edges=chain([1, 2, 3, 4, 5]), also=held_earlier(1, 4),
         must_see={1: all4, 2: THREE}, must_miss={3: THREE, 4: THREE, 5: THREE}, authored=True,
         notes="Same route as C2. From the LLM's answer on the facts travel under keys the model does not recognise; "
               "only lineage carries the label to V1.")

    case("C4", "held for the second vendor", legs({1: "s1 s2 s3 s4", 8: SEEN, 9: MISS, 10: MISS, 11: MISS}, flag=11),
         cell="trunk/one/root-to-right-leaf", verdict=ba, edges=chain([1, 8, 9, 10, 11]), also=held_earlier(1, 10),
         must_see={1: all4, 8: THREE}, must_miss={9: THREE, 10: THREE, 11: THREE}, authored=True,
         notes="Root to the right leaf, the left branch benign: legs 4 to 7 carry nothing, the agent held the facts "
               "across a clean exchange.")

    case("C5", "returned by the first vendor", legs({6: "s1 s2 s3 s4", 7: SEEN, 8: MISS, 9: MISS, 10: MISS, 11: MISS}, flag=11),
         cell="trunk/one/leaf-to-leaf", verdict=ba, edges=chain([6, 7, 8, 9, 10, 11]), also=held_earlier(7, 10),
         must_see={6: all4, 7: THREE}, must_miss={8: THREE, 9: THREE, 10: THREE, 11: THREE}, authored=True,
         notes="Leaf to leaf, starting from a response: born in the insurer's answer. T1 forgets, so leg 7 inherits leg 6 "
               "only through the invocation-scope fix of #348 (a response inherits what its child calls returned).")

    # C6: ..V1.. stands for legs 5 and 6 (invisible); numbering stays the trunk's, the verdict at 11.
    c6 = (f"U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[{SEEN} -], A>L[{MISS} -], L>A[{MISS} -], "
          f"A>T2[{MISS} -], T2>V2[{MISS} -]⚑, V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-]")
    case("C6", "returned unseen", c6, cell="trunk/one/leaf-to-leaf (leaf invisible)", verdict=ba,
         edges=chain([7, 8, 9, 10, 11]), must_see={7: THREE}, must_miss={8: THREE, 9: THREE, 10: THREE, 11: THREE},
         authored=True, also=held_earlier(7, 10),
         notes="C5 with V1 off the wire: T1 is the leaf, the origin is T1's answer at leg 7; legs 5 and 6 are the unseen hop.")

    # K: conversation 1 is C6's flow with no verdict (the three leave through V2 at leg 11);
    # conversation 2 (legs 17-32) is C5's shape with the origin one conversation back.
    conv1 = (f"U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[{SEEN} -], A>L[{SEEN} -], L>A[{SEEN} -], "
             f"A>T2[{SEEN} -], T2>V2[{SEEN} -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-]")
    conv1_chain = chain([7, 8, 9, 10, 11])
    ks = {
        "K0": ([], "reborn, nothing", "allow"),
        "K1": (["s1", "s2", "s3"], "reborn, all three", "block"),
        "K2": (["s1"], "reborn, the SSN alone", "block"),
        "K3": (["s2"], "reborn, the MRN alone", "block"),
        "K4": (["s3"], "reborn, the birthdate alone", "allow"),
        "K5": (["s1", "s2"], "reborn, SSN and MRN", "block"),
        "K6": (["s1", "s3"], "reborn, SSN and birthdate", "block"),
        "K7": (["s2", "s3"], "reborn, MRN and birthdate", "block"),
    }
    miss_key = {"s1": "s1@subject", "s2": "s2@account", "s3": "s3@ref"}
    for id_, (k, title, want) in ks.items():
        kk = " ".join(miss_key[u] for u in k)
        body = f"{kk} -" if kk else "-"
        conv2 = (f"U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[{body}], T2>A[{body}], A>L[{body}], L>A[{body}], "
                 f"A>T3[{body}], T3>V3[{body}]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]")
        line = f"{conv1} ~10min> {conv2}"
        edges = list(conv1_chain)
        if k and want == "block":
            # within conversation 2: 22 -> 23 -> ... -> 27; the crossing attaches at T2's answer (23), one hop late,
            # from T2's last holding in conversation 1 (11).
            edges += chain([22, 23, 24, 25, 26, 27], units=k) + [f"{u} 11>23" for u in k]
        must_miss = {str(i): list(k) for i in range(22, 28)} if k else {}
        wanted = {"on": want, "off": "allow"}
        pending = PENDING if want == "block" else None
        n = len(k)
        nb = {0: "—", 1: "0 of 2", 2: "1 of 2", 3: "2 of 2"}[n]
        case(id_, title, line, cell="trunk/cross", verdict=wanted, edges=edges, must_see={}, must_miss=must_miss,
             pending=pending, authored=bool(k), also=held_earlier(7, 10),
             notes=f"Conversation 2 is C5's shape; V2 returns {' '.join(k) or 'nothing'} (neighbourhood {nb}). The classifier "
                   f"labels nothing in conversation 2, so only a crossing from conversation 1 can produce the alert at V3. "
                   f"Wanted under person-anchored: {want}.")

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

if __name__ == "__main__":
    main()
