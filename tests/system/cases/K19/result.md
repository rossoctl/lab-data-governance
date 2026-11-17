# K19 — exactly M earlier conversations

cell `across/common/at-M`  ·  status **GREEN**  ·  classifier **authored**: the model's findings subtracted at legs [22, 23, 24, 25]

```
U>A[s1 -], A>T2[s1 -], T2>V2[s1 -], V2>T2[-], T2>A[-], A>U[-] ~2min> U>A[s1 -], A>T2[s1 -], T2>V2[s1 -], V2>T2[-], T2>A[-], A>U[-] ~2min> U>A[s1 -], A>T2[s1 -], T2>V2[s1 -], V2>T2[-], T2>A[-], A>U[-] ~2min> U>A[-], A>T2[-], T2>V2[-], V2>T2[s1@subject -], T2>A[s1@subject -], A>T3[s1@subject -], T3>V3[s1@subject -]⚑, V3>T3[-], T3>A[-], A>U[-]
```

```
expected
conversation 1
              1           2           3           4           5           6           
              U>A         A>T2        T2>V2       V2>T2       T2>A        A>U         
SSN     s1    ●           ━━━━━━━━━━━●━━━━━━━━━━━●·           ·           ·           

conversation 2
              7           8           9           10          11          12          
              U>A         A>T2        T2>V2       V2>T2       T2>A        A>U         
SSN     s1    ┈┈┈┈┈┈┈┈┈┈┈●━━━━━━━━━━━●━━━━━━━━━━━●·           ·           ·           

conversation 3
              13          14          15          16          17          18          
              U>A         A>T2        T2>V2       V2>T2       T2>A        A>U         
SSN     s1    ┈┈┈┈┈┈┈┈┈┈┈●━━━━━━━━━━━●━━━━━━━━━━━●·           ·           ·           

conversation 4
              19     20     21     22     23     24     25     26     27     28     
              U>A    A>T2   T2>V2  V2>T2  T2>A   A>T3   T3>V3⚑ V3>T3  T3>A   A>U    
SSN     s1    ·      ·      ·      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      

observed
conversation 1
              1           2           3           4           5           6           
              U>A         A>T2        T2>V2       V2>T2       T2>A        A>U         
SSN     s1    ●           ━━━━━━━━━━━●━━━━━━━━━━━●·           ·           ·           

conversation 2
              7           8           9           10          11          12          
              U>A         A>T2        T2>V2       V2>T2       T2>A        A>U         
SSN     s1    ┈┈┈┈┈┈┈┈┈┈┈●━━━━━━━━━━━●━━━━━━━━━━━●·           ·           ·           

conversation 3
              13          14          15          16          17          18          
              U>A         A>T2        T2>V2       V2>T2       T2>A        A>U         
SSN     s1    ┈┈┈┈┈┈┈┈┈┈┈●━━━━━━━━━━━●━━━━━━━━━━━●·           ·           ·           

conversation 4
              19     20     21     22     23     24     25     26     27     28     
              U>A    A>T2   T2>V2  V2>T2  T2>A   A>T3   T3>V3⚑ V3>T3  T3>A   A>U    
SSN     s1    ·      ·      ·      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      
verdict at 25: expected off=allow / conv=allow / cross=block, observed off=allow / conv=allow / cross=block
```

**Rows (cross run).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A | RESTRICTED SSN=123-45-6789 | — | — |
| 2 | A>T2 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 1) | — |
| 3 | T2>V2 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 2) | — |
| 4 | V2>T2 | PUBLIC  | — | — |
| 5 | T2>A | PUBLIC  | — | — |
| 6 | A>U | PUBLIC  | — | — |
| 7 | U>A | RESTRICTED SSN=123-45-6789 | — | — |
| 8 | A>T2 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 7) | — |
| 9 | T2>V2 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 8) | — |
| 10 | V2>T2 | PUBLIC  | — | — |
| 11 | T2>A | PUBLIC  | — | — |
| 12 | A>U | PUBLIC  | — | — |
| 13 | U>A | RESTRICTED SSN=123-45-6789 | — | — |
| 14 | A>T2 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 13) | — |
| 15 | T2>V2 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 14) | — |
| 16 | V2>T2 | PUBLIC  | — | — |
| 17 | T2>A | PUBLIC  | — | — |
| 18 | A>U | PUBLIC  | — | — |
| 19 | U>A | PUBLIC  | — | — |
| 20 | A>T2 | PUBLIC  | — | — |
| 21 | T2>V2 | PUBLIC  | — | — |
| 22 | V2>T2 | PUBLIC  | s1=123-45-6789 (SSN, from 15, vo; crossed vo ambiguous), s1=123-45-6789 (SSN, from 9, vo; crossed vo ambiguous), s1=123-45-6789 (SSN, from 3, vo; crossed vo ambiguous) | — |
| 23 | T2>A | PUBLIC  | s1=123-45-6789 (SSN, from 22; crossed vo ambiguous), s1=123-45-6789 (SSN, from 22; crossed vo ambiguous), s1=123-45-6789 (SSN, from 22; crossed vo ambiguous) | — |
| 24 | A>T3 | PUBLIC  | s1=123-45-6789 (SSN, from 23; crossed vo ambiguous), s1=123-45-6789 (SSN, from 23; crossed vo ambiguous), s1=123-45-6789 (SSN, from 23; crossed vo ambiguous) | — |
| 25 | T3>V3⚑ | PUBLIC  | s1=123-45-6789 (SSN, from 24; crossed vo ambiguous), s1=123-45-6789 (SSN, from 24; crossed vo ambiguous), s1=123-45-6789 (SSN, from 24; crossed vo ambiguous) | critical block ['T-SENS'] v1 |
| 26 | V3>T3 | PUBLIC  | — | — |
| 27 | T3>A | PUBLIC  | — | — |
| 28 | A>U | PUBLIC  | — | — |

Risk observer calls: 38

**Notes.** Three earlier conversations (M = 3, R9) each send V2 the user's SSN; the fourth gets it back. Held from exactly M conversations it is not a common value: three edges, value-only, ambiguous; it blocks.

