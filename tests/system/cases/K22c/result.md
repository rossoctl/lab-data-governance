# K22c — the same tool, declared conversation

cell `across/scope/declared-store`  ·  status **GREEN**  ·  classifier **authored**: the model's findings subtracted at legs [7, 8, 9]

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
SSN     s1    ·      ·      ·      ●      ·      ·      ━━━━━━○━━━━━━○━━━━━━○·      ·      ·      
MRN     s2    ·      ·      ·      ●      ·      ·      ━━━━━━○━━━━━━○━━━━━━○·      ·      ·      
verdict at 9: expected off=allow / conv=block / cross=block, observed off=allow / conv=block / cross=block
```

**Rows (cross run).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A | PUBLIC  | — | — |
| 2 | A>T2 | PUBLIC  | — | — |
| 3 | T2>V1 | PUBLIC  | — | — |
| 4 | V1>T2 | RESTRICTED MRN=4471820 SSN=123-45-6789 | — | — |
| 5 | T2>A | PUBLIC  | — | — |
| 6 | A>T2 | PUBLIC  | — | — |
| 7 | T2>A | PUBLIC  | s2=4471820 (MRN, from 4), s1=123-45-6789 (SSN, from 4) | — |
| 8 | A>T3 | PUBLIC  | s2=4471820 (MRN, from 7), s1=123-45-6789 (SSN, from 7) | — |
| 9 | T3>V3⚑ | PUBLIC  | s2=4471820 (MRN, from 8), s1=123-45-6789 (SSN, from 8) | critical block ['T-SENS'] v1 |
| 10 | V3>T3 | PUBLIC  | — | — |
| 11 | T3>A | PUBLIC  | — | — |
| 12 | A>U | PUBLIC  | — | — |

Risk observer calls: 15

**Notes.** K22b with T2 declared conversation scope: inside one conversation it pools its two calls, so what it received in the first (leg 4) reaches what it returns in the second (leg 7), an edge of the conversation's own, carried to V3, which blocks once inherited labels are on. The declaration is what draws the edges: if it did not reach the processor, the line would be K22b's.

