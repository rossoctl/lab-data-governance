# K25 — the identifier's label removed from conversation 1

cell `across/order`  ·  status **GREEN**  ·  classifier **authored**: the model's findings subtracted at legs [22, 23, 24, 25, 26, 27]

```
U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2 s3 -], A>L[s1 s2 s3 -], L>A[s1 s2 s3 -], A>T2[s1 s2 s3 -], T2>V2[s1 s2 s3 -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-] ~10min> U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[s1@subject -], T2>A[s1@subject -], A>L[s1@subject -], L>A[s1@subject -], A>T3[s1@subject -], T3>V3[s1@subject -]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]
```

```
expected
conversation 1
              1      2      3      4      5-6    7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   ..V1.. T1>A   A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·             ●      ┈┈┈┈┈┈●┈┈┈┈┈┈●┈┈┈┈┈┈●┈┈┈┈┈┈●·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·             ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

conversation 2
              17     18     19     20     21     22     23     24     25     26     27     28     29     30     31     32     
              U>A    A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>T3   T3>V3⚑ V3>T3  T3>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

observed
conversation 1
              1      2      3      4      5-6    7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   ..V1.. T1>A   A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·             ○      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·             ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

conversation 2
              17     18     19     20     21     22     23     24     25     26     27     28     29     30     31     32     
              U>A    A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>T3   T3>V3⚑ V3>T3  T3>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
verdict at 27: expected off=allow / conv=allow / cross=[block, allow], observed off=allow / conv=allow / cross=[block, allow]
```

**Rows (cross run).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A | PUBLIC  | — | — |
| 2 | A>L | PUBLIC  | — | — |
| 3 | L>A | PUBLIC  | — | — |
| 4 | A>T1 | PUBLIC  | — | — |
| 5 | ..V1.. | — | — | — |
| 7 | T1>A | RESTRICTED DOB=1961-03-14 MRN=4471820 | — | — |
| 8 | A>L | RESTRICTED DOB=1961-03-14 MRN=4471820 | s3=1961-03-14 (DOB, from 7), s2=4471820 (MRN, from 7) | — |
| 9 | L>A | RESTRICTED DOB=1961-03-14 MRN=4471820 | s3=1961-03-14 (DOB, from 8), s2=4471820 (MRN, from 8) | — |
| 10 | A>T2 | RESTRICTED DOB=1961-03-14 MRN=4471820 | s3=1961-03-14 (DOB, from 9), s3=1961-03-14 (DOB, from 7), s2=4471820 (MRN, from 9), s2=4471820 (MRN, from 7) | — |
| 11 | T2>V2 | RESTRICTED DOB=1961-03-14 MRN=4471820 | s3=1961-03-14 (DOB, from 10), s2=4471820 (MRN, from 10) | — |
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
| 22 | V2>T2 | PUBLIC  | — | — |
| 23 | T2>A | PUBLIC  | — | — |
| 24 | A>L | PUBLIC  | — | — |
| 25 | L>A | PUBLIC  | — | — |
| 26 | A>T3 | PUBLIC  | — | — |
| 27 | T3>V3⚑ | PUBLIC  | — | none allow [] v2 |
| 28 | V3>T3 | PUBLIC  | — | — |
| 29 | T3>A | PUBLIC  | — | — |
| 30 | A>L | PUBLIC  | — | — |
| 31 | L>A | PUBLIC  | — | — |
| 32 | A>U | PUBLIC  | — | — |

Risk observer calls: 46

**Notes.** K2, then every payload is classified again by ADR-0024's runbook, the model now reading legs 7-11 without the SSN, and conversation 1 is queued and derived again (the runbook re-derives nothing by itself). Its SSN occurrence at V2 is gone, which queues conversation 2: it loses the crossing and every label that rested on it, and the flagged decision gets one new version (block, then allow). The expected rows are the final state, with no SSN edge in conversation 1 either. Conversation 1 is derived once more: it changes no occurrence, queues nothing, and writes no version. The history, every version, predicted from the mechanism before the run: v1 block at the first drain (one record evidences both legs of the flagged exchange), v2 allow at the second (the correction's recompute), nothing at the third (no summary changed, no recompute): [block, allow]. The classifier premise states the first classification; the rows are checked against it with the reclassified cells unlabelled.

