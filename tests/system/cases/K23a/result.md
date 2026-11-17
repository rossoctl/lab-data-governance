# K23a — a limit fires: holdings per entity

cell `across/limits/holdings`  ·  status **PENDING**

```
U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s3 -], A>L[s1 s3 -], L>A[s1 s3 -], A>T2[s1 s3 -], T2>V2[s1 s3 -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-]
```

```
expected
              1      2      3      4      5-6    7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   ..V1.. T1>A   A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·             ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      

observed
(not run)
```

**Reasons.** pending: the limits are not configuration on this branch; held, not run · held, not run: needs the limits as configuration: set in run_case before the first send

**Notes.** V2 may hold one value; at leg 11 it receives two. Its holdings are cut, and the leg that would have registered the second is marked incomplete, naming the limit. Which value is kept is not stated: no later leg depends on it here.

