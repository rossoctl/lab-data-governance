# K21 — the partner returns it in a later call

cell `across/scope/same-conversation`  ·  status **GREEN**  ·  classifier **authored**: the model's findings subtracted at legs [6, 10, 11, 12, 13]

```
U>A[-], A>T2[-], T2>V1[-], V1>T2[s1 -], T2>V2[s1 -], V2>T2[s1@subject -], T2>A[-], A>T3[-], T3>V2[-], V2>T3[s1@subject -], T3>A[s1@subject -], A>T4[s1@subject -], T4>V4[s1@subject -]⚑, V4>T4[-], T4>A[-], A>U[-]
```

```
expected
              1      2      3      4      5      6      7      8      9      10     11     12     13     14     15     16     
              U>A    A>T2   T2>V1  V1>T2  T2>V2  V2>T2  T2>A   A>T3   T3>V2  V2>T3  T3>A   A>T4   T4>V4⚑ V4>T4  T4>A   A>U    
SSN     s1    ·      ·      ·      ●      ━━━━━━●━━━━━━○·      ·      ·      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      

observed
              1      2      3      4      5      6      7      8      9      10     11     12     13     14     15     16     
              U>A    A>T2   T2>V1  V1>T2  T2>V2  V2>T2  T2>A   A>T3   T3>V2  V2>T3  T3>A   A>T4   T4>V4⚑ V4>T4  T4>A   A>U    
SSN     s1    ·      ·      ·      ●      ━━━━━━●━━━━━━○·      ·      ·      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      
verdict at 13: expected off=allow / conv=allow / cross=block, observed off=allow / conv=allow / cross=block
```

**Rows (cross run).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A | PUBLIC  | — | — |
| 2 | A>T2 | PUBLIC  | — | — |
| 3 | T2>V1 | PUBLIC  | — | — |
| 4 | V1>T2 | RESTRICTED SSN=123-45-6789 | — | — |
| 5 | T2>V2 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 4) | — |
| 6 | V2>T2 | PUBLIC  | s1=123-45-6789 (SSN, from 5) | — |
| 7 | T2>A | PUBLIC  | — | — |
| 8 | A>T3 | PUBLIC  | — | — |
| 9 | T3>V2 | PUBLIC  | — | — |
| 10 | V2>T3 | PUBLIC  | s1=123-45-6789 (SSN, from 5, vo; crossed vo) | — |
| 11 | T3>A | PUBLIC  | s1=123-45-6789 (SSN, from 10; crossed vo) | — |
| 12 | A>T4 | PUBLIC  | s1=123-45-6789 (SSN, from 11; crossed vo) | — |
| 13 | T4>V4⚑ | PUBLIC  | s1=123-45-6789 (SSN, from 12; crossed vo) | critical block ['T-SENS'] v1 |
| 14 | V4>T4 | PUBLIC  | — | — |
| 15 | T4>A | PUBLIC  | — | — |
| 16 | A>U | PUBLIC  | — | — |

Risk observer calls: 21

**Notes.** One conversation. T2 gets the SSN from V1 and sends it to V2, which echoes it inside the same call (5>6, the conversation's own edge). In a later call, from T3, V2 returns it again. V2 is persistent: the same mechanism links the two calls (R7), one edge, value-only; inside one call the existing edge stands alone, never a second row (5>6 is not drawn again as a crossing).

