# C6 — returned unseen

cell `trunk/one/leaf-to-leaf (leaf invisible)`  ·  status **GREEN**  ·  classifier **authored**: the model's findings subtracted at legs [8, 9, 10, 11]

```
U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2 s3 -], A>L[s1@subject s2@account s3@ref -], L>A[s1@subject s2@account s3@ref -], A>T2[s1@subject s2@account s3@ref -], T2>V2[s1@subject s2@account s3@ref -]⚑, V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-]
```

```
expected
              1      2      3      4      5-6    7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   ..V1.. T1>A   A>L    L>A    A>T2   T2>V2⚑ V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·             ●      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·             ●      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·             ●      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·             ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

observed
              1      2      3      4      5-6    7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   ..V1.. T1>A   A>L    L>A    A>T2   T2>V2⚑ V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·             ●      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·             ●      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·             ●      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·             ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
neighbourhood at 8: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 9: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 10: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 11: s1 2 of 2, s2 2 of 2, s3 2 of 2
verdict at 11: expected on=block / off=allow, observed on=block / off=allow
```

**Rows (inherited labels on).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A | PUBLIC  | — | — |
| 2 | A>L | PUBLIC  | — | — |
| 3 | L>A | PUBLIC  | — | — |
| 4 | A>T1 | PUBLIC  | — | — |
| 5 | ..V1.. | — | — | — |
| 7 | T1>A | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | — | — |
| 8 | A>L | PUBLIC  | s3=1961-03-14 (DOB, from 7), s2=4471820 (MRN, from 7), s1=123-45-6789 (SSN, from 7) | — |
| 9 | L>A | PUBLIC  | s3=1961-03-14 (DOB, from 8), s2=4471820 (MRN, from 8), s1=123-45-6789 (SSN, from 8) | — |
| 10 | A>T2 | PUBLIC  | s3=1961-03-14 (DOB, from 7), s2=4471820 (MRN, from 7), s1=123-45-6789 (SSN, from 7), s3=1961-03-14 (DOB, from 9), s2=4471820 (MRN, from 9), s1=123-45-6789 (SSN, from 9) | — |
| 11 | T2>V2⚑ | PUBLIC  | s3=1961-03-14 (DOB, from 10), s2=4471820 (MRN, from 10), s1=123-45-6789 (SSN, from 10) | critical block ['T-SENS'] v1 |
| 12 | V2>T2 | PUBLIC  | — | — |
| 13 | T2>A | PUBLIC  | — | — |
| 14 | A>L | PUBLIC  | — | — |
| 15 | L>A | PUBLIC  | — | — |
| 16 | A>U | PUBLIC  | — | — |

OPA calls: 17

**Notes.** C5 with V1 off the wire: T1 is the leaf, the origin is T1's answer at leg 7; legs 5 and 6 are the unseen hop.

