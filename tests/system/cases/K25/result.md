# K25 — the identifier's label removed from conversation 1

cell `across/order`  ·  status **PENDING**  ·  classifier **authored**: the model's findings subtracted at legs [22, 23, 24, 25, 26, 27]

```
U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2 s3 -], A>L[s1 s2 s3 -], L>A[s1 s2 s3 -], A>T2[s1 s2 s3 -], T2>V2[s1 s2 s3 -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-] ~10min> U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[s1@subject -], T2>A[s1@subject -], A>L[s1@subject -], L>A[s1@subject -], A>T3[s1@subject -], T3>V3[s1@subject -]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]
```

```
expected
conversation 1
              1      2      3      4      5-6    7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   ..V1.. T1>A   A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·             ●      ┈┈┈┈┈┈●┈┈┈┈┈┈●┈┈┈┈┈┈●┈┈┈┈┈┈●·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·             ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

conversation 2
              17     18     19     20     21     22     23     24     25     26     27     28     29     30     31     32     
              U>A    A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>T3   T3>V3⚑ V3>T3  T3>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

observed
(not run)
verdict at 27: expected off=allow / conv=allow / cross=allow, observed off=? / conv=? / cross=?
```

**Reasons.** pending: a classification replaced and its conversation derived again is not on this branch; held, not run · held, not run: needs a classification replaced and its conversation derived again: a reclassify step

**Notes.** K2, then conversation 1 is classified again without the SSN's findings (the truncate-and-reclassify of ADR-0024) and derived again. Conversation 2 loses the crossing and every label that rested on it: the expected rows are the final state, with no SSN edge in conversation 1 either. Deriving once more changes nothing. The classifier premise states the first classification.

