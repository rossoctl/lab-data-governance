# K29 — the classifier alone blocks

cell `across/tiers`  ·  status **PENDING**

```
U>A[s1 s2 s3 s4 -], A>L[s1 s2 s3 -], L>A[s1 s2 s3 -], A>T1[s1 s2 s3 -], T1>V1[s1 s2 s3 -]⚑, V1>T1[-], T1>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-]
```

```
expected
              1      2      3      4      5      6      7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   T1>V1⚑ V1>T1  T1>A   A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
MRN     s2    ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ●      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

observed
(not run)
verdict at 5: expected off=[block tier 0] / conv=[block tier 0] / cross=[block tier 0], observed off=? / conv=? / cross=?
```

**Reasons.** pending: the verdict tiers are not on this branch; held, not run · held, not run: needs the verdict tiers: read by verdict_at

**Notes.** C2: the classifier labels the three on the flagged leg. The decision is block at tier 0, before any lineage is asked, in every mode.

