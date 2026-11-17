# K22a — a tool declared a store

cell `across/scope/declared-store`  ·  status **PENDING**  ·  classifier **authored**: the model's findings subtracted at legs [7, 8, 9]

```
U>A[-], A>T2[-], T2>V1[-], V1>T2[s1 s2 -], T2>A[-], A>T2[-], T2>A[s1@subject s2@account -], A>T3[s1@subject s2@account -], T3>V3[s1@subject s2@account -]⚑, V3>T3[-], T3>A[-], A>U[-]
```

```
expected
              1      2      3      4      5      6      7      8      9      10     11     12     
              U>A    A>T2   T2>V1  V1>T2  T2>A   A>T2   T2>A   A>T3   T3>V3⚑ V3>T3  T3>A   A>U    
SSN     s1    ·      ·      ·      ●      ·      ·      ━━━━━━○━━━━━━○━━━━━━○·      ·      ·      
MRN     s2    ·      ·      ·      ●      ·      ·      ━━━━━━○━━━━━━○━━━━━━○·      ·      ·      

observed
(not run)
verdict at 9: expected off=allow / conv=allow / cross=block, observed off=? / conv=? / cross=?
```

**Reasons.** pending: the scope declared per entity is not on this branch; held, not run · held, not run: needs the memory scope declared per entity: applied in run_case before the first send

**Notes.** T2 is declared persistent (a store). In its first call it fetches the SSN and the MRN from V1 (it received them); in a second call it returns them. One edge per anchoring unit, corroborated (two identifiers of one record), carried to V3, which blocks.

