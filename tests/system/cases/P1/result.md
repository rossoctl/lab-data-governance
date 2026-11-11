# P1 — one call, two partners

cell `trunk/control/one-call-two-partners`  ·  status **GREEN**  ·  classifier **authored**: the model's findings subtracted at legs [5, 7]

```
U>A[s1 -], A>T1[s1 -], T1>V1[s1 -], V1>T1[s2 -], T1>V2[s1@subject s2@account -]⚑, V2>T1[-], T1>A[s1@subject s2@account -], A>U[-]
```

```
expected
              1           2           3           4           5           6           7           8           
              U>A         A>T1        T1>V1       V1>T1       T1>V2⚑      V2>T1       T1>A        A>U         
SSN     s1    ●           ━━━━━━━━━━━●━━━━━━━━━━━●·           ━━━━━━━━━━━○·           ━━━━━━━━━━━○·           
MRN     s2    ·           ·           ·           ●           ━━━━━━━━━━━○·           ━━━━━━━━━━━○·           
DOB     s3    ·           ·           ·           ·           ·           ·           ·           ·           
DIS     s4    ·           ·           ·           ·           ·           ·           ·           ·           

observed
              1           2           3           4           5           6           7           8           
              U>A         A>T1        T1>V1       V1>T1       T1>V2⚑      V2>T1       T1>A        A>U         
SSN     s1    ●           ━━━━━━━━━━━●━━━━━━━━━━━●·           ━━━━━━━━━━━○·           ━━━━━━━━━━━○·           
MRN     s2    ·           ·           ·           ●           ━━━━━━━━━━━○·           ━━━━━━━━━━━○·           
DOB     s3    ·           ·           ·           ·           ·           ·           ·           ·           
DIS     s4    ·           ·           ·           ·           ·           ·           ·           ·           
neighbourhood at 2: s1 0 of 0
neighbourhood at 3: s1 0 of 0
neighbourhood at 5: s1 0 of 0, s2 0 of 0
neighbourhood at 7: s1 0 of 0, s2 0 of 0
verdict at 5: expected on=block / off=allow, observed on=block / off=allow
```

**Rows (inherited labels on).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A | RESTRICTED SSN=123-45-6789 | — | — |
| 2 | A>T1 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 1) | — |
| 3 | T1>V1 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 2) | — |
| 4 | V1>T1 | RESTRICTED MRN=4471820 | — | — |
| 5 | T1>V2⚑ | PUBLIC  | s1=123-45-6789 (SSN, from 2), s2=4471820 (MRN, from 4) | critical block ['T-SENS'] v1 |
| 6 | V2>T1 | PUBLIC  | — | — |
| 7 | T1>A | PUBLIC  | s1=123-45-6789 (SSN, from 2), s2=4471820 (MRN, from 4) | — |
| 8 | A>U | PUBLIC  | — | — |

OPA calls: 11

**Notes.** Picture 1 of #348. Inside A's call, T1 asks V1, then asks V2 with what V1 returned, then answers A. T1's second request inherits from the call that opened it (s1 2>5) and from V1's answer inside the call (s2 4>5); its answer carries both (2>7, 4>7). Before #348 the fold drew s2 4>5 only, and no 4>7.

