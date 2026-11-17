# K14 — two patients, two records

cell `across/rule/same-record`  ·  status **GREEN**  ·  classifier **authored**: the model's findings subtracted at legs [22, 23, 24, 25, 26, 27]

```
U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[{s1 s3} {s5 s6} -], A>L[{s1 s3} {s5 s6} -], L>A[{s1 s3} {s5 s6} -], A>T2[{s1 s3} {s5 s6} -], T2>V2[{s1 s3} {s5 s6} -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-] ~10min> U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[s1@subject s6@since -], T2>A[s1@subject s6@since -], A>L[s1@subject s6@since -], L>A[s1@subject s6@since -], A>T3[s1@subject s6@since -], T3>V3[s1@subject s6@since -]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]
```

```
expected
conversation 1
              1      2      3      4      5-6    7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   ..V1.. T1>A   A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
SSN     s5    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
DOB     s6    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      

conversation 2
              17     18     19     20     21     22     23     24     25     26     27     28     29     30     31     32     
              U>A    A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>T3   T3>V3⚑ V3>T3  T3>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
SSN     s5    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s6    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      

observed
conversation 1
              1      2      3      4      5-6    7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   ..V1.. T1>A   A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
SSN     s5    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
DOB     s6    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      

conversation 2
              17     18     19     20     21     22     23     24     25     26     27     28     29     30     31     32     
              U>A    A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>T3   T3>V3⚑ V3>T3  T3>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
SSN     s5    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s6    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
verdict at 27: expected off=allow / conv=allow / cross=block, observed off=allow / conv=allow / cross=block
```

**Rows (cross run).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A | PUBLIC  | — | — |
| 2 | A>L | PUBLIC  | — | — |
| 3 | L>A | PUBLIC  | — | — |
| 4 | A>T1 | PUBLIC  | — | — |
| 5 | ..V1.. | — | — | — |
| 7 | T1>A | RESTRICTED DOB=1961-03-14 SSN=123-45-6789 DOB=1978-11-02 SSN=987-65-4321 | — | — |
| 8 | A>L | RESTRICTED DOB=1961-03-14 SSN=123-45-6789 DOB=1978-11-02 SSN=987-65-4321 | s3=1961-03-14 (DOB, from 7), s1=123-45-6789 (SSN, from 7), s6=1978-11-02 (DOB, from 7), s5=987-65-4321 (SSN, from 7) | — |
| 9 | L>A | RESTRICTED DOB=1961-03-14 SSN=123-45-6789 DOB=1978-11-02 SSN=987-65-4321 | s3=1961-03-14 (DOB, from 8), s1=123-45-6789 (SSN, from 8), s6=1978-11-02 (DOB, from 8), s5=987-65-4321 (SSN, from 8) | — |
| 10 | A>T2 | RESTRICTED DOB=1961-03-14 SSN=123-45-6789 DOB=1978-11-02 SSN=987-65-4321 | s3=1961-03-14 (DOB, from 9), s3=1961-03-14 (DOB, from 7), s1=123-45-6789 (SSN, from 9), s1=123-45-6789 (SSN, from 7), s6=1978-11-02 (DOB, from 9), s6=1978-11-02 (DOB, from 7), s5=987-65-4321 (SSN, from 9), s5=987-65-4321 (SSN, from 7) | — |
| 11 | T2>V2 | RESTRICTED DOB=1961-03-14 SSN=123-45-6789 DOB=1978-11-02 SSN=987-65-4321 | s3=1961-03-14 (DOB, from 10), s1=123-45-6789 (SSN, from 10), s6=1978-11-02 (DOB, from 10), s5=987-65-4321 (SSN, from 10) | — |
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
| 22 | V2>T2 | PUBLIC  | s1=123-45-6789 (SSN, from 11, vo; crossed vo) | — |
| 23 | T2>A | PUBLIC  | s1=123-45-6789 (SSN, from 22; crossed vo) | — |
| 24 | A>L | PUBLIC  | s1=123-45-6789 (SSN, from 23; crossed vo) | — |
| 25 | L>A | PUBLIC  | s1=123-45-6789 (SSN, from 24; crossed vo) | — |
| 26 | A>T3 | PUBLIC  | s1=123-45-6789 (SSN, from 25; crossed vo), s1=123-45-6789 (SSN, from 23; crossed vo) | — |
| 27 | T3>V3⚑ | PUBLIC  | s1=123-45-6789 (SSN, from 26; crossed vo) | critical block ['T-SENS'] v1 |
| 28 | V3>T3 | PUBLIC  | — | — |
| 29 | T3>A | PUBLIC  | — | — |
| 30 | A>L | PUBLIC  | — | — |
| 31 | L>A | PUBLIC  | — | — |
| 32 | A>U | PUBLIC  | — | — |

Risk observer calls: 38

**Notes.** Conversation 1 sends V2 a listing of two patients, one record each: {SSN A, birthdate A} and {SSN B, birthdate B}. V2 returns A's SSN and B's birthdate. They left in different records, so they do not corroborate (R4): A's SSN crosses value-only, B's birthdate has no anchor in its record and draws nothing. Never corroborated; it blocks on the SSN (R5).

