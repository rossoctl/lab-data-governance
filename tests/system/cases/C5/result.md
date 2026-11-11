# C5 — returned by the first vendor

cell `trunk/one/leaf-to-leaf`  ·  status **GREEN**  ·  classifier **authored**: the model's findings subtracted at legs [8, 9, 10, 11]

```
U>A[-], A>L[-], L>A[-], A>T1[-], T1>V1[-], V1>T1[s1 s2 s3 s4 -], T1>A[s1 s2 s3 -], A>L[s1@subject s2@account s3@ref -], L>A[s1@subject s2@account s3@ref -], A>T2[s1@subject s2@account s3@ref -], T2>V2[s1@subject s2@account s3@ref -]⚑, V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-]
```

```
expected
              1      2      3      4      5      6      7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   T1>V1  V1>T1  T1>A   A>L    L>A    A>T2   T2>V2⚑ V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ●      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ●      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ●      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ●      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

observed
              1      2      3      4      5      6      7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   T1>V1  V1>T1  T1>A   A>L    L>A    A>T2   T2>V2⚑ V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ●      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ●      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ●      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ●      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
neighbourhood at 7: s1 2 of 3, s2 2 of 3, s3 2 of 3
neighbourhood at 8: s1 2 of 3, s2 2 of 3, s3 2 of 3
neighbourhood at 9: s1 2 of 3, s2 2 of 3, s3 2 of 3
neighbourhood at 10: s1 2 of 3, s2 2 of 3, s3 2 of 3
neighbourhood at 11: s1 2 of 3, s2 2 of 3, s3 2 of 3
verdict at 11: expected on=block / off=allow, observed on=block / off=allow
```

**Reasons.** also drawn, from an earlier holding (where it was born): s1 7>10, s2 7>10, s3 7>10

**Rows (inherited labels on).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A | PUBLIC  | — | — |
| 2 | A>L | PUBLIC  | — | — |
| 3 | L>A | PUBLIC  | — | — |
| 4 | A>T1 | PUBLIC  | — | — |
| 5 | T1>V1 | PUBLIC  | — | — |
| 6 | V1>T1 | RESTRICTED DIS=type 2 diabetes DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | — | — |
| 7 | T1>A | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 6), s2=4471820 (MRN, from 6), s1=123-45-6789 (SSN, from 6) | — |
| 8 | A>L | PUBLIC  | s3=1961-03-14 (DOB, from 7), s2=4471820 (MRN, from 7), s1=123-45-6789 (SSN, from 7) | — |
| 9 | L>A | PUBLIC  | s3=1961-03-14 (DOB, from 8), s2=4471820 (MRN, from 8), s1=123-45-6789 (SSN, from 8) | — |
| 10 | A>T2 | PUBLIC  | s3=1961-03-14 (DOB, from 7), s2=4471820 (MRN, from 7), s1=123-45-6789 (SSN, from 7), s3=1961-03-14 (DOB, from 9), s2=4471820 (MRN, from 9), s1=123-45-6789 (SSN, from 9) | — |
| 11 | T2>V2⚑ | PUBLIC  | s3=1961-03-14 (DOB, from 10), s2=4471820 (MRN, from 10), s1=123-45-6789 (SSN, from 10) | critical block ['T-SENS'] v1 |
| 12 | V2>T2 | PUBLIC  | — | — |
| 13 | T2>A | PUBLIC  | — | — |
| 14 | A>L | PUBLIC  | — | — |
| 15 | L>A | PUBLIC  | — | — |
| 16 | A>U | PUBLIC  | — | — |

**Timing (on).** total 1.5477 s: reset 0.0828, receiver 0.0558, interactions 0.868, data_lineage 0.1641, classification 0.0362, label_lineage 0.0193, leg_ready+risk 0.3031, read 0.0184
OPA calls: 20

**Notes.** Leaf to leaf, starting from a response: born in the insurer's answer. T1 forgets, so leg 7 inherits leg 6 only through the invocation-scope fix of #348 (a response inherits what its child calls returned).

