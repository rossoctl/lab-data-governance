# K22b — the same tool, not declared

cell `across/scope/declared-store`  ·  status **GREEN**  ·  classifier **authored**: the model's findings subtracted at legs [7, 8, 9]

```
U>A[-], A>T2[-], T2>V1[-], V1>T2[s1 s2 -], T2>A[-], A>T2[-], T2>A[s1@subject s2@account -], A>T3[s1@subject s2@account -], T3>V3[s1@subject s2@account -]⚑, V3>T3[-], T3>A[-], A>U[-]
```

```
expected
              1      2      3      4      5      6      7      8      9      10     11     12     
              U>A    A>T2   T2>V1  V1>T2  T2>A   A>T2   T2>A   A>T3   T3>V3⚑ V3>T3  T3>A   A>U    
SSN     s1    ·      ·      ·      ●      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      
MRN     s2    ·      ·      ·      ●      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      

observed
              1      2      3      4      5      6      7      8      9      10     11     12     
              U>A    A>T2   T2>V1  V1>T2  T2>A   A>T2   T2>A   A>T3   T3>V3⚑ V3>T3  T3>A   A>U    
SSN     s1    ·      ·      ·      ●      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      
MRN     s2    ·      ·      ·      ●      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      
verdict at 9: expected off=allow / conv=allow / cross=allow, observed off=allow / conv=allow / cross=allow
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
| 7 | T2>A | PUBLIC  | — | — |
| 8 | A>T3 | PUBLIC  | — | — |
| 9 | T3>V3⚑ | PUBLIC  | — | none allow [] v1 |
| 10 | V3>T3 | PUBLIC  | — | — |
| 11 | T3>A | PUBLIC  | — | — |
| 12 | A>U | PUBLIC  | — | — |

Risk observer calls: 12

**Notes.** K22a without the declaration: a tool is call scope by default and keeps nothing between its two calls. No edge, allow. Its deliberate break (a tool persistent by default) turns it red.

