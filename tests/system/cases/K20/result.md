# K20 — M+1 earlier conversations

cell `across/common/beyond-M`  ·  status **GREEN**  ·  classifier **authored**: the model's findings subtracted at legs [32, 33, 34, 35]

```
U>A[s1 -], A>T2[s1 -], T2>V2[s1 -], V2>T2[-], T2>V2[s1 -], V2>T2[-], T2>V2[s1 -], V2>T2[-], T2>A[-], A>U[-] ~2min> U>A[s1 -], A>T2[s1 -], T2>V2[s1 -], V2>T2[-], T2>A[-], A>U[-] ~2min> U>A[s1 -], A>T2[s1 -], T2>V2[s1 -], V2>T2[-], T2>A[-], A>U[-] ~2min> U>A[s1 -], A>T2[s1 -], T2>V2[s1 -], V2>T2[-], T2>A[-], A>U[-] ~2min> U>A[-], A>T2[-], T2>V2[-], V2>T2[s1@subject -], T2>A[s1@subject -], A>T3[s1@subject -], T3>V3[s1@subject -]⚑, V3>T3[-], T3>A[-], A>U[-]
```

```
expected
conversation 1
              1      2      3      4      5      6      7      8      9      10     
              U>A    A>T2   T2>V2  V2>T2  T2>V2  V2>T2  T2>V2  V2>T2  T2>A   A>U    
SSN     s1    ●      ━━━━━━●━━━━━━●·      ━━━━━━●·      ━━━━━━●·      ·      ·      

conversation 2
              11          12          13          14          15          16          
              U>A         A>T2        T2>V2       V2>T2       T2>A        A>U         
SSN     s1    ┈┈┈┈┈┈┈┈┈┈┈●━━━━━━━━━━━●━━━━━━━━━━━●·           ·           ·           

conversation 3
              17          18          19          20          21          22          
              U>A         A>T2        T2>V2       V2>T2       T2>A        A>U         
SSN     s1    ┈┈┈┈┈┈┈┈┈┈┈●━━━━━━━━━━━●━━━━━━━━━━━●·           ·           ·           

conversation 4
              23          24          25          26          27          28          
              U>A         A>T2        T2>V2       V2>T2       T2>A        A>U         
SSN     s1    ┈┈┈┈┈┈┈┈┈┈┈●━━━━━━━━━━━●━━━━━━━━━━━●·           ·           ·           

conversation 5
              29     30     31     32     33     34     35     36     37     38     
              U>A    A>T2   T2>V2  V2>T2  T2>A   A>T3   T3>V3⚑ V3>T3  T3>A   A>U    
SSN     s1    ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      

observed
conversation 1
              1      2      3      4      5      6      7      8      9      10     
              U>A    A>T2   T2>V2  V2>T2  T2>V2  V2>T2  T2>V2  V2>T2  T2>A   A>U    
SSN     s1    ●      ━━━━━━●━━━━━━●·      ━━━━━━●·      ━━━━━━●·      ·      ·      

conversation 2
              11          12          13          14          15          16          
              U>A         A>T2        T2>V2       V2>T2       T2>A        A>U         
SSN     s1    ┈┈┈┈┈┈┈┈┈┈┈●━━━━━━━━━━━●━━━━━━━━━━━●·           ·           ·           

conversation 3
              17          18          19          20          21          22          
              U>A         A>T2        T2>V2       V2>T2       T2>A        A>U         
SSN     s1    ┈┈┈┈┈┈┈┈┈┈┈●━━━━━━━━━━━●━━━━━━━━━━━●·           ·           ·           

conversation 4
              23          24          25          26          27          28          
              U>A         A>T2        T2>V2       V2>T2       T2>A        A>U         
SSN     s1    ┈┈┈┈┈┈┈┈┈┈┈●━━━━━━━━━━━●━━━━━━━━━━━●·           ·           ·           

conversation 5
              29     30     31     32     33     34     35     36     37     38     
              U>A    A>T2   T2>V2  V2>T2  T2>A   A>T3   T3>V3⚑ V3>T3  T3>A   A>U    
SSN     s1    ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      
verdict at 35: expected off=allow / conv=allow / cross=allow, observed off=allow / conv=allow / cross=allow
```

**Rows (cross run).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A | RESTRICTED SSN=123-45-6789 | — | — |
| 2 | A>T2 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 1) | — |
| 3 | T2>V2 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 2) | — |
| 4 | V2>T2 | PUBLIC  | — | — |
| 5 | T2>V2 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 2) | — |
| 6 | V2>T2 | PUBLIC  | — | — |
| 7 | T2>V2 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 2) | — |
| 8 | V2>T2 | PUBLIC  | — | — |
| 9 | T2>A | PUBLIC  | — | — |
| 10 | A>U | PUBLIC  | — | — |
| 11 | U>A | RESTRICTED SSN=123-45-6789 | — | — |
| 12 | A>T2 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 11) | — |
| 13 | T2>V2 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 12) | — |
| 14 | V2>T2 | PUBLIC  | — | — |
| 15 | T2>A | PUBLIC  | — | — |
| 16 | A>U | PUBLIC  | — | — |
| 17 | U>A | RESTRICTED SSN=123-45-6789 | — | — |
| 18 | A>T2 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 17) | — |
| 19 | T2>V2 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 18) | — |
| 20 | V2>T2 | PUBLIC  | — | — |
| 21 | T2>A | PUBLIC  | — | — |
| 22 | A>U | PUBLIC  | — | — |
| 23 | U>A | RESTRICTED SSN=123-45-6789 | — | — |
| 24 | A>T2 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 23) | — |
| 25 | T2>V2 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 24) | — |
| 26 | V2>T2 | PUBLIC  | — | — |
| 27 | T2>A | PUBLIC  | — | — |
| 28 | A>U | PUBLIC  | — | — |
| 29 | U>A | PUBLIC  | — | — |
| 30 | A>T2 | PUBLIC  | — | — |
| 31 | T2>V2 | PUBLIC  | — | — |
| 32 | V2>T2 | PUBLIC  | — | — |
| 33 | T2>A | PUBLIC  | — | — |
| 34 | A>T3 | PUBLIC  | — | — |
| 35 | T3>V3⚑ | PUBLIC  | — | none allow [] v1 |
| 36 | V3>T3 | PUBLIC  | — | — |
| 37 | T3>A | PUBLIC  | — | — |
| 38 | A>U | PUBLIC  | — | — |

Marks (cross run): leg 32 [common 4]

Risk observer calls: 52

**Notes.** Four earlier conversations send V2 the SSN, the first of them three times (three calls in one call of T2); the fifth gets it back. Counted by distinct conversation the value is held from 4 > M = 3 conversations (R2): a common value, no edge, the count on the leg. Counted by leg it would be 6: the stated count tells the two apart.

