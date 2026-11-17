# K12 — the partner declared call scope

cell `across/rule/scope`  ·  status **PENDING**  ·  classifier **authored**: the model's findings subtracted at legs [22, 23, 24, 25, 26, 27]

```
U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2 s3 -], A>L[s1 s2 s3 -], L>A[s1 s2 s3 -], A>T2[s1 s2 s3 -], T2>V2[s1 s2 s3 -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-] ~10min> U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[s1@subject s2@account s3@ref -], T2>A[s1@subject s2@account s3@ref -], A>L[s1@subject s2@account s3@ref -], L>A[s1@subject s2@account s3@ref -], A>T3[s1@subject s2@account s3@ref -], T3>V3[s1@subject s2@account s3@ref -]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]
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
              U>A    A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>T3   T3>V3⚑ V3>T3  T3>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

observed
(not run)
verdict at 27: expected off=allow / conv=allow / cross=allow, observed off=? / conv=? / cross=?
```

**Reasons.** pending: the scope declared per entity is not on this branch; held, not run · held, not run: needs the memory scope declared per entity: applied in run_case before the first send

**Notes.** K1 with V2 declared call scope: it remembers nothing past one call, so nothing it returns in conversation 2 can cross. No crossing, allow.

