# K15 — the record number read as an operational identifier

cell `across/rule/label-type`  ·  status **GREEN**  ·  classifier **authored**: the model's findings subtracted at legs [7, 8, 9, 10, 22, 23, 24, 25, 26, 27]

```
U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2@external_id s3 -], A>L[s1 s2@external_id s3 -], L>A[s1 s2@external_id s3 -], A>T2[s1 s2@external_id s3 -], T2>V2[s1 s2@external_id s3 -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-] ~10min> U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[s2@account -], T2>A[s2@account -], A>L[s2@account -], L>A[s2@account -], A>T3[s2@account -], T3>V3[s2@account -]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]
```

```
expected
conversation 1
              1      2      3      4      5-6    7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   ..V1.. T1>A   A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·             ○      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈●·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·             ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

conversation 2
              17     18     19     20     21     22     23     24     25     26     27     28     29     30     31     32     
              U>A    A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>T3   T3>V3⚑ V3>T3  T3>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

observed
conversation 1
              1      2      3      4      5-6    7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   ..V1.. T1>A   A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·             ○      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈●·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·             ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

conversation 2
              17     18     19     20     21     22     23     24     25     26     27     28     29     30     31     32     
              U>A    A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>T3   T3>V3⚑ V3>T3  T3>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
verdict at 27: expected off=allow / conv=allow / cross=allow, observed off=allow / conv=allow / cross=allow
```

**Rows (cross run).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A | PUBLIC  | — | — |
| 2 | A>L | PUBLIC  | — | — |
| 3 | L>A | PUBLIC  | — | — |
| 4 | A>T1 | PUBLIC  | — | — |
| 5 | ..V1.. | — | — | — |
| 7 | T1>A | RESTRICTED DOB=1961-03-14 SSN=123-45-6789 | — | — |
| 8 | A>L | RESTRICTED DOB=1961-03-14 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 7), s1=123-45-6789 (SSN, from 7) | — |
| 9 | L>A | RESTRICTED DOB=1961-03-14 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 8), s1=123-45-6789 (SSN, from 8) | — |
| 10 | A>T2 | RESTRICTED DOB=1961-03-14 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 7), s1=123-45-6789 (SSN, from 7), s3=1961-03-14 (DOB, from 9), s1=123-45-6789 (SSN, from 9) | — |
| 11 | T2>V2 | RESTRICTED DOB=1961-03-14 OPID=4471820 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 10), s1=123-45-6789 (SSN, from 10) | — |
| 12 | V2>T2 | PUBLIC  | — | — |
| 13 | T2>A | PUBLIC  | — | — |
| 14 | A>L | PUBLIC  | — | — |
| 15 | L>A | PUBLIC  | — | — |
| 16 | A>U | PUBLIC  | — | — |
| 17 | U>A | PUBLIC  | — | — |
| 18 | A>L | PUBLIC  | — | — |
| 19 | L>A | PUBLIC  | — | — |
| 20 | A>T2 | PUBLIC  | — | — |
| 21 | T2>V2 | PUBLIC  | — | — |
| 22 | V2>T2 | PUBLIC  | — | — |
| 23 | T2>A | PUBLIC  | — | — |
| 24 | A>L | PUBLIC  | — | — |
| 25 | L>A | PUBLIC  | — | — |
| 26 | A>T3 | PUBLIC  | — | — |
| 27 | T3>V3⚑ | PUBLIC  | — | none allow [] v1 |
| 28 | V3>T3 | PUBLIC  | — | — |
| 29 | T3>A | PUBLIC  | — | — |
| 30 | A>L | PUBLIC  | — | — |
| 31 | L>A | PUBLIC  | — | — |
| 32 | A>U | PUBLIC  | — | — |

Risk observer calls: 33

**Notes.** Conversation 1 sends the MRN under external_id; at leg 11, where V2 receives it, the model labels it an operational identifier (OPID), not a person identifier. Whether a value anchors follows the type it was received with (R8): no crossing, allow. The model's findings on the MRN at legs 7-10 are subtracted (authored): it reads it as OPID there too, except at leg 8 (DLN), which would give the value a second origin of another type. Green on this branch only because nothing crosses at all here; the case means something once the crossing core exists, and its deliberate break (the anchor rule off) must turn it red.

