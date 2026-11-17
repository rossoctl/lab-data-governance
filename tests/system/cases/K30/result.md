# K30 — the cross tier comes after the answer

cell `across/tiers`  ·  status **GREEN**  ·  classifier **authored**: the model's findings subtracted at legs [22, 23, 24, 25, 26, 27]

```
U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2 s3 -], A>L[s1 s2 s3 -], L>A[s1 s2 s3 -], A>T2[s1 s2 s3 -], T2>V2[s1 s2 s3 -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-] ~10min> U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[s1@subject -], T2>A[s1@subject -], A>L[s1@subject -], L>A[s1@subject -], A>T3[s1@subject -], T3>V3[s1@subject -]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]
```

```
expected
conversation 1
              1      2      3      4      5-6    7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   ..V1.. T1>A   A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·             ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

conversation 2
              17     18     19     20     21     22     23     24     25     26     27     28     29     30     31     32     
              U>A    A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>T3   T3>V3⚑ V3>T3  T3>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

observed
conversation 1
              1      2      3      4      5-6    7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   ..V1.. T1>A   A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·             ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

conversation 2
              17     18     19     20     21     22     23     24     25     26     27     28     29     30     31     32     
              U>A    A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>T3   T3>V3⚑ V3>T3  T3>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
verdict at 27: expected off=[allow tier 0] / conv=[allow tier 1] / cross=[allow tier 1, block tier 2 late], observed off=[allow tier 0] / conv=[allow tier 1] / cross=[allow tier 1, block tier 2 late]
```

**Rows (cross run).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A | PUBLIC  | — | — |
| 2 | A>L | PUBLIC  | — | — |
| 3 | L>A | PUBLIC  | — | — |
| 4 | A>T1 | PUBLIC  | — | — |
| 5 | ..V1.. | — | — | — |
| 7 | T1>A | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | — | — |
| 8 | A>L | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 7), s2=4471820 (MRN, from 7), s1=123-45-6789 (SSN, from 7) | — |
| 9 | L>A | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 8), s2=4471820 (MRN, from 8), s1=123-45-6789 (SSN, from 8) | — |
| 10 | A>T2 | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 7), s3=1961-03-14 (DOB, from 9), s2=4471820 (MRN, from 7), s2=4471820 (MRN, from 9), s1=123-45-6789 (SSN, from 7), s1=123-45-6789 (SSN, from 9) | — |
| 11 | T2>V2 | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 10), s2=4471820 (MRN, from 10), s1=123-45-6789 (SSN, from 10) | — |
| 12 | V2>T2 | PUBLIC  | — | — |
| 13 | T2>A | PUBLIC  | — | — |
| 14 | A>L | PUBLIC  | — | — |
| 15 | L>A | PUBLIC  | — | — |
| 16 | A>U | PUBLIC  | — | — |
| 17 | U>A | PUBLIC  | — | — |
| 18 | A>L | PUBLIC  | — | — |
| 19 | L>A | PUBLIC  | — | — |
| 20 | A>T2 | PUBLIC  | — | — |
| 21 | T2>V2 | PUBLIC  | — | — |
| 22 | V2>T2 | PUBLIC  | s1=123-45-6789 (SSN, from 11, vo; crossed vo) | — |
| 23 | T2>A | PUBLIC  | s1=123-45-6789 (SSN, from 22; crossed vo) | — |
| 24 | A>L | PUBLIC  | s1=123-45-6789 (SSN, from 23; crossed vo) | — |
| 25 | L>A | PUBLIC  | s1=123-45-6789 (SSN, from 24; crossed vo) | — |
| 26 | A>T3 | PUBLIC  | s1=123-45-6789 (SSN, from 25; crossed vo), s1=123-45-6789 (SSN, from 23; crossed vo) | — |
| 27 | T3>V3⚑ | PUBLIC  | s1=123-45-6789 (SSN, from 26; crossed vo) | critical block ['T-SENS'] v2 |
| 28 | V3>T3 | PUBLIC  | — | — |
| 29 | T3>A | PUBLIC  | — | — |
| 30 | A>L | PUBLIC  | — | — |
| 31 | L>A | PUBLIC  | — | — |
| 32 | A>U | PUBLIC  | — | — |

Risk observer calls: 38

**Notes.** K2 with tier 2 (the crossed labels) absent at the first decision because the cross-conversation switch was off, not because it was slow: the timing of a gate's limit is a live run's, which this harness cannot show. Both conversations are sent and drained with the switch off; it is turned on (as a restart would) and both are derived again: switching on does not look back (R6), so conversation 1 registers its occurrences only when derived again, and conversation 2, derived while the switch was off, wrote no send rows a correction could find, so it is queued too. The crossing is drawn and the flagged decision recomputed. The history, every version, predicted from the mechanism before the run. cross: v1 allow at tier 1 (tiers 0 and 1 were on, nothing in conversation 2 is labelled), v2 block at tier 2 (the crossed SSN), stricter than v1: a late finding; v1 stands, the finding does not reach back. conv: the switch stays off whatever a step says, so tiers 0 and 1 only: one version, [allow tier 1]. off: inherited labels off, tier 0 only; the crossing is drawn (the switch governs label lineage) but reaches no decision: one version, [allow tier 0].

