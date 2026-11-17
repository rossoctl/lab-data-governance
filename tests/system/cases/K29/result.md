# K29 — the classifier alone blocks

cell `across/tiers`  ·  status **GREEN**

```
U>A[s1 s2 s3 s4 -], A>L[s1 s2 s3 -], L>A[s1 s2 s3 -], A>T1[s1 s2 s3 -], T1>V1[s1 s2 s3 -]⚑, V1>T1[-], T1>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-]
```

```
expected
              1      2      3      4      5      6      7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   T1>V1⚑ V1>T1  T1>A   A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
MRN     s2    ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ●      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

observed
              1      2      3      4      5      6      7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   T1>V1⚑ V1>T1  T1>A   A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
MRN     s2    ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ●      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
verdict at 5: expected off=[block tier 0] / conv=[block tier 0] / cross=[block tier 0], observed off=[block tier 0] / conv=[block tier 0] / cross=[block tier 0]
```

**Rows (cross run).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A | RESTRICTED DIS=type 2 diabetes DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | — | — |
| 2 | A>L | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 1), s2=4471820 (MRN, from 1), s1=123-45-6789 (SSN, from 1) | — |
| 3 | L>A | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 2), s2=4471820 (MRN, from 2), s1=123-45-6789 (SSN, from 2) | — |
| 4 | A>T1 | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 1), s3=1961-03-14 (DOB, from 3), s2=4471820 (MRN, from 1), s2=4471820 (MRN, from 3), s1=123-45-6789 (SSN, from 1), s1=123-45-6789 (SSN, from 3) | — |
| 5 | T1>V1⚑ | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 4), s2=4471820 (MRN, from 4), s1=123-45-6789 (SSN, from 4) | critical block ['T-SENS'] v1 |
| 6 | V1>T1 | PUBLIC  | — | — |
| 7 | T1>A | PUBLIC  | — | — |
| 8 | A>L | PUBLIC  | — | — |
| 9 | L>A | PUBLIC  | — | — |
| 10 | A>T2 | PUBLIC  | — | — |
| 11 | T2>V2 | PUBLIC  | — | — |
| 12 | V2>T2 | PUBLIC  | — | — |
| 13 | T2>A | PUBLIC  | — | — |
| 14 | A>L | PUBLIC  | — | — |
| 15 | L>A | PUBLIC  | — | — |
| 16 | A>U | PUBLIC  | — | — |

Risk observer calls: 19

**Notes.** C2: the classifier labels the three on the flagged leg, so tier 0 (classification alone) blocks. The history, every version, predicted from the mechanism before the run: one version in every mode (both legs of the flagged exchange are on the wire when leg-ready delivers its request; the response computes the same record). The policy is asked per tier on that snapshot (off: tier 0 only; conv: 0 and 1, the chain's labels reaching leg 5; cross: 0, 1 and 2, tier 2's input equal to tier 1's), every tier blocks, and a block is decided at the first tier that blocks: [block tier 0] in every mode. That the answer comes before any lineage is asked is timing, which this harness does not show: the record says the classifier alone decided it.

