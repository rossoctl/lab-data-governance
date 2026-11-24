# K22a — a tool declared a store

cell `across/scope/declared-store`  ·  status **PENDING**  ·  classifier **authored**: the model's findings subtracted at legs [7, 8, 9]

```
U>A[-], A>T2[-], T2>V1[-], V1>T2[s1 s2 -], T2>A[-], A>T2[-], T2>A[s1@subject s2@account -], A>T3[s1@subject s2@account -], T3>V3[s1@subject s2@account -]⚑, V3>T3[-], T3>A[-], A>U[-]
```

```
expected
              1      2      3      4      5      6      7      8      9      10     11     12     
              U>A    A>T2   T2>V1  V1>T2  T2>A   A>T2   T2>A   A>T3   T3>V3⚑ V3>T3  T3>A   A>U    
SSN     s1    ·      ·      ·      ●      ·      ·      ━━━━━━○━━━━━━○━━━━━━○·      ·      ·      
MRN     s2    ·      ·      ·      ●      ·      ·      ━━━━━━○━━━━━━○━━━━━━○·      ·      ·      

observed
              1      2      3      4      5      6      7      8      9      10     11     12     
              U>A    A>T2   T2>V1  V1>T2  T2>A   A>T2   T2>A   A>T3   T3>V3⚑ V3>T3  T3>A   A>U    
SSN     s1    ·      ·      ·      ●      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      
MRN     s2    ·      ·      ·      ●      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      
verdict at 9: expected off=allow / conv=allow / cross=block, observed off=allow / conv=allow / cross=allow
```

**Reasons.** pending: no crossing on this branch: the crossing core draws the crossing and the edges that rest on it; the level on its way carries crossed and its level to every later leg and into the policy input, so the cross run blocks at the flagged leg. The off and conv runs already match · verdict cross: want block, got allow · edges missing: s1 4>7 co, s1 7>8, s1 8>9, s2 4>7 co, s2 7>8, s2 8>9 · evidence at 7 s1: want [crossed co], got [] · evidence at 7 s2: want [crossed co], got [] · evidence at 8 s1: want [crossed co], got [] · evidence at 8 s2: want [crossed co], got [] · evidence at 9 s1: want [crossed co], got [] · evidence at 9 s2: want [crossed co], got []

**Rows (cross run).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A | PUBLIC  | — | — |
| 2 | A>T2 | PUBLIC  | — | — |
| 3 | T2>V1 | PUBLIC  | — | — |
| 4 | V1>T2 | RESTRICTED MRN=4471820 SSN=123-45-6789 | — | — |
| 5 | T2>A | PUBLIC  | — | — |
| 6 | A>T2 | PUBLIC  | — | — |
| 7 | T2>A | PUBLIC  | — | — |
| 8 | A>T3 | PUBLIC  | — | — |
| 9 | T3>V3⚑ | PUBLIC  | — | none allow [] v1 |
| 10 | V3>T3 | PUBLIC  | — | — |
| 11 | T3>A | PUBLIC  | — | — |
| 12 | A>U | PUBLIC  | — | — |

Risk observer calls: 12

**Notes.** T2 is declared persistent (a store). In its first call it fetches the SSN and the MRN from V1 (it received them); in a second call it returns them. One edge per anchoring unit, corroborated (two identifiers of one record), carried to V3, which blocks.

