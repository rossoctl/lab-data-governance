# P3 — two callers, one value

cell `trunk/control/two-callers-one-value`  ·  status **GREEN**  ·  classifier **authored**: the model's findings subtracted at legs [5, 7, 9, 10]

```
U>A1[s1 -], U>A2[s1 -], A1>T1[s1 -], A2>T1[s1 -], T1(A1)>V1[s1@subject -]⚑, V1>T1[-], T1(A2)>V2[s1@subject -], V2>T1[-], T1>A1[s1@subject -], T1>A2[s1@subject -], A1>U[-], A2>U[-]
```

```
expected
              1      2      3      4      5      6      7      8      9      10     11     12     
              U>A1   U>A2   A1>T1  A2>T1  T1>V1⚑ V1>T1  T1>V2  V2>T1  T1>A1  T1>A2  A1>U   A2>U   
SSN     s1    ●      ┈┈┈┈┈┈●━━━━━━●━━━━━━●━━━━━━○·      ━━━━━━○·      ━━━━━━○━━━━━━○·      ·      
MRN     s2    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

observed
              1      2      3      4      5      6      7      8      9      10     11     12     
              U>A1   U>A2   A1>T1  A2>T1  T1>V1⚑ V1>T1  T1>V2  V2>T1  T1>A1  T1>A2  A1>U   A2>U   
SSN     s1    ●      ┈┈┈┈┈┈●━━━━━━●━━━━━━●━━━━━━○·      ━━━━━━○·      ━━━━━━○━━━━━━○·      ·      
MRN     s2    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
neighbourhood at 3: s1 0 of 0
neighbourhood at 4: s1 0 of 0
neighbourhood at 5: s1 0 of 0
neighbourhood at 7: s1 0 of 0
neighbourhood at 9: s1 0 of 0
neighbourhood at 10: s1 0 of 0
verdict at 5: expected on=block / off=allow, observed on=block / off=allow
```

**Rows (inherited labels on).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A1 | RESTRICTED SSN=123-45-6789 | — | — |
| 2 | U>A2 | RESTRICTED SSN=123-45-6789 | — | — |
| 3 | A1>T1 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 1) | — |
| 4 | A2>T1 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 2) | — |
| 5 | T1>V1⚑ | PUBLIC  | s1=123-45-6789 (SSN, from 3) | critical block ['T-SENS'] v1 |
| 6 | V1>T1 | PUBLIC  | — | — |
| 7 | T1>V2 | PUBLIC  | s1=123-45-6789 (SSN, from 4) | — |
| 8 | V2>T1 | PUBLIC  | — | — |
| 9 | T1>A1 | PUBLIC  | s1=123-45-6789 (SSN, from 3) | — |
| 10 | T1>A2 | PUBLIC  | s1=123-45-6789 (SSN, from 4) | — |
| 11 | A1>U | PUBLIC  | — | — |
| 12 | A2>U | PUBLIC  | — | — |

OPA calls: 16

**Notes.** Picture 2 of #348 with the same value in both calls, so a wrong parent shows as an edge that should not exist: T1(A1)>V1 must inherit from A1's request (3>5), not from A2's, which arrived later (4>5). Before #348 the fold drew 4>5, and 3>5 and 4>7 were missing.

