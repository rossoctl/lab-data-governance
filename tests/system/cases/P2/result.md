# P2 — two callers in flight

cell `trunk/control/two-callers-in-flight`  ·  status **GREEN**  ·  classifier **authored**: the model's findings subtracted at legs [5, 7, 9, 10]

```
U>A1[s1 -], U>A2[s2 -], A1>T1[s1 -], A2>T1[s2 -], T1(A1)>V1[s1@subject -]⚑, V1>T1[-], T1(A2)>V2[s2@account -], V2>T1[-], T1>A1[s1@subject -], T1>A2[s2@account -], A1>U[-], A2>U[-]
```

```
              1      2      3      4      5      6      7      8      9      10     11     12     
              U>A1   U>A2   A1>T1  A2>T1  T1>V1⚑ V1>T1  T1>V2  V2>T1  T1>A1  T1>A2  A1>U   A2>U   
SSN     s1    ●      ·      ━━━━━━●·      ━━━━━━○·      ·      ·      ━━━━━━○·      ·      ·      
MRN     s2    ·      ●      ·      ━━━━━━●·      ·      ━━━━━━○·      ·      ━━━━━━○·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
neighbourhood at 3: s1 0 of 0
neighbourhood at 4: s2 0 of 0
neighbourhood at 5: s1 0 of 0
neighbourhood at 7: s2 0 of 0
neighbourhood at 9: s1 0 of 0
neighbourhood at 10: s2 0 of 0
verdict at 5: on=block / off=allow
```

**Rows (inherited labels on).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A1 | RESTRICTED SSN=123-45-6789 | — | — |
| 2 | U>A2 | RESTRICTED MRN=4471820 | — | — |
| 3 | A1>T1 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 1) | — |
| 4 | A2>T1 | RESTRICTED MRN=4471820 | s2=4471820 (MRN, from 2) | — |
| 5 | T1>V1⚑ | PUBLIC  | s1=123-45-6789 (SSN, from 3) | critical block ['T-SENS'] v1 |
| 6 | V1>T1 | PUBLIC  | — | — |
| 7 | T1>V2 | PUBLIC  | s2=4471820 (MRN, from 4) | — |
| 8 | V2>T1 | PUBLIC  | — | — |
| 9 | T1>A1 | PUBLIC  | s1=123-45-6789 (SSN, from 3) | — |
| 10 | T1>A2 | PUBLIC  | s2=4471820 (MRN, from 4) | — |
| 11 | A1>U | PUBLIC  | — | — |
| 12 | A2>U | PUBLIC  | — | — |

**Timing (on).** total 1.2751 s: reset 0.1051, receiver 0.0561, interactions 0.7389, data_lineage 0.125, classification 0.0309, label_lineage 0.0185, leg_ready+risk 0.1835, read 0.0171
OPA calls: 16

**Notes.** Picture 2 of #348. A1 and A2 call T1; A2's request arrives before T1 serves A1. T1(A1)>V1 belongs to A1's call, so it carries s1 (3>5) and nothing of A2's; before #348 the fold routed it from the latest inbound (A2's) and V1 saw no label: verdict allow.

