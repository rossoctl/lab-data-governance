# K17 — a relay through two crossings

cell `across/level/relay`  ·  status **PENDING**  ·  classifier **authored**: the model's findings subtracted at legs [22, 23, 24, 25, 26, 27, 38, 39, 40, 41, 42, 43]

```
U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2 s3 -], A>L[s1 s2 s3 -], L>A[s1 s2 s3 -], A>T2[s1 s2 s3 -], T2>V2[s1 s2 s3 -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-] ~2min> U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[s1@subject s2@account s3@ref -], T2>A[s1@subject s2@account s3@ref -], A>L[s1@subject s2@account s3@ref -], L>A[s1@subject s2@account s3@ref -], A>T3[s1@subject s2@account s3@ref -], T3>V3[s1@subject s2@account s3@ref -], V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-] ~2min> U>A[-], A>L[-], L>A[-], A>T3[-], T3>V3[-], V3>T3[s1@subject -], T3>A[s1@subject -], A>L[s1@subject -], L>A[s1@subject -], A>T4[s1@subject -], T4>V4[s1@subject -]⚑, V4>T4[-], T4>A[-], A>L[-], L>A[-], A>U[-]
```

```
expected
conversation 1
              1      2      3      4      5-6    7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   ..V1.. T1>A   A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·             ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

conversation 2
              17     18     19     20     21     22     23     24     25     26     27     28     29     30     31     32     
              U>A    A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>T3   T3>V3  V3>T3  T3>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

conversation 3
              33     34     35     36     37     38     39     40     41     42     43     44     45     46     47     48     
              U>A    A>L    L>A    A>T3   T3>V3  V3>T3  T3>A   A>L    L>A    A>T4   T4>V4⚑ V4>T4  T4>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

observed
conversation 1
              1      2      3      4      5-6    7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   ..V1.. T1>A   A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·             ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

conversation 2
              17     18     19     20     21     22     23     24     25     26     27     28     29     30     31     32     
              U>A    A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>T3   T3>V3  V3>T3  T3>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

conversation 3
              33     34     35     36     37     38     39     40     41     42     43     44     45     46     47     48     
              U>A    A>L    L>A    A>T3   T3>V3  V3>T3  T3>A   A>L    L>A    A>T4   T4>V4⚑ V4>T4  T4>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
verdict at 43: expected off=allow / conv=allow / cross=block, observed off=allow / conv=allow / cross=allow
```

**Reasons.** pending: no crossing on this branch: the crossing core draws the crossing and the edges that rest on it; the level on its way carries crossed and its level to every later leg and into the policy input, so the cross run blocks at the flagged leg. The off and conv runs already match · verdict cross: want block, got allow · edges missing: s1 11>22 co, s1 22>23, s1 23>24, s1 23>26, s1 24>25, s1 25>26, s1 26>27, s1 27>38 vo, s1 38>39, s1 39>40, s1 39>42, s1 40>41, s1 41>42, s1 42>43, s2 11>22 co, s2 22>23, s2 23>24, s2 23>26, s2 24>25, s2 25>26, s2 26>27, s3 11>22 co, s3 22>23, s3 23>24, s3 23>26, s3 24>25, s3 25>26, s3 26>27 · evidence at 22 s1: want [crossed co], got [] · evidence at 22 s2: want [crossed co], got [] · evidence at 22 s3: want [crossed co], got [] · evidence at 23 s1: want [crossed co], got [] · evidence at 23 s2: want [crossed co], got [] · evidence at 23 s3: want [crossed co], got [] · evidence at 24 s1: want [crossed co], got [] · evidence at 24 s2: want [crossed co], got [] · evidence at 24 s3: want [crossed co], got [] · evidence at 25 s1: want [crossed co], got [] · evidence at 25 s2: want [crossed co], got [] · evidence at 25 s3: want [crossed co], got [] · evidence at 26 s1: want [crossed co], got [] · evidence at 26 s2: want [crossed co], got [] · evidence at 26 s3: want [crossed co], got [] · evidence at 27 s1: want [crossed co], got [] · evidence at 27 s2: want [crossed co], got [] · evidence at 27 s3: want [crossed co], got [] · evidence at 38 s1: want [crossed vo], got [] · evidence at 39 s1: want [crossed vo], got [] · evidence at 40 s1: want [crossed vo], got [] · evidence at 41 s1: want [crossed vo], got [] · evidence at 42 s1: want [crossed vo], got [] · evidence at 43 s1: want [crossed vo], got []

**Rows (cross run).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A | PUBLIC  | — | — |
| 2 | A>L | PUBLIC  | — | — |
| 3 | L>A | PUBLIC  | — | — |
| 4 | A>T1 | PUBLIC  | — | — |
| 5 | ..V1.. | — | — | — |
| 7 | T1>A | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | — | — |
| 8 | A>L | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 7), s2=4471820 (MRN, from 7), s1=123-45-6789 (SSN, from 7) | — |
| 9 | L>A | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 8), s2=4471820 (MRN, from 8), s1=123-45-6789 (SSN, from 8) | — |
| 10 | A>T2 | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 7), s2=4471820 (MRN, from 7), s1=123-45-6789 (SSN, from 7), s3=1961-03-14 (DOB, from 9), s2=4471820 (MRN, from 9), s1=123-45-6789 (SSN, from 9) | — |
| 11 | T2>V2 | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 10), s2=4471820 (MRN, from 10), s1=123-45-6789 (SSN, from 10) | — |
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
| 27 | T3>V3 | PUBLIC  | — | — |
| 28 | V3>T3 | PUBLIC  | — | — |
| 29 | T3>A | PUBLIC  | — | — |
| 30 | A>L | PUBLIC  | — | — |
| 31 | L>A | PUBLIC  | — | — |
| 32 | A>U | PUBLIC  | — | — |
| 33 | U>A | PUBLIC  | — | — |
| 34 | A>L | PUBLIC  | — | — |
| 35 | L>A | PUBLIC  | — | — |
| 36 | A>T3 | PUBLIC  | — | — |
| 37 | T3>V3 | PUBLIC  | — | — |
| 38 | V3>T3 | PUBLIC  | — | — |
| 39 | T3>A | PUBLIC  | — | — |
| 40 | A>L | PUBLIC  | — | — |
| 41 | L>A | PUBLIC  | — | — |
| 42 | A>T4 | PUBLIC  | — | — |
| 43 | T4>V4⚑ | PUBLIC  | — | none allow [] v1 |
| 44 | V4>T4 | PUBLIC  | — | — |
| 45 | T4>A | PUBLIC  | — | — |
| 46 | A>L | PUBLIC  | — | — |
| 47 | L>A | PUBLIC  | — | — |
| 48 | A>U | PUBLIC  | — | — |

Risk observer calls: 49

**Notes.** Conversation 1 to 2 by a corroborated crossing at V2 (as K1): V3 receives the three at leg 27 as crossed, corroborated, and holds them (what is registered includes a crossing's label). Conversation 3 gets the SSN alone back from V3: value-only. Along the path the weakest level holds: the label reaches conversation 3 as crossed, value-only.

