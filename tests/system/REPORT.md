# System tests — label lineage, exact match

*Generated 2026-11-11 12:01 IST by `pytest tests/system`; environment {}.*

| case | cell | classifier | line | on / off | status | note |
|---|---|---|---|---|---|---|
| [C1](cases/C1/result.md) | `trunk/one/quiet` | real | `U>A[-], A>L[-], L>A[-], A>T1[-], T1>V1[-]⚑, V1>T1[-], T1>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-]` | allow / allow | **GREEN** |  |
| [C2](cases/C2/result.md) | `trunk/one/root-to-left-leaf` | real | `U>A[s1 s2 s3 s4 -], A>L[s1 s2 s3 -], L>A[s1 s2 s3 -], A>T1[s1 s2 s3 -], T1>V1[s1 s2 s3 -]⚑, V1>T1[-], T1>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-]` | block / block | **GREEN** |  |
| [C3](cases/C3/result.md) | `trunk/one/root-to-left-leaf` | authored | `U>A[s1 s2 s3 s4 -], A>L[s1 s2 s3 -], L>A[s1@subject s2@account s3@ref -], A>T1[s1@subject s2@account s3@ref -], T1>V1[s1@subject s2@account s3@ref -]⚑, V1>T1[-], T1>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-]` | block / allow | **GREEN** |  |
| [C4](cases/C4/result.md) | `trunk/one/root-to-right-leaf` | authored | `U>A[s1 s2 s3 s4 -], A>L[-], L>A[-], A>T1[-], T1>V1[-], V1>T1[-], T1>A[-], A>L[s1 s2 s3 -], L>A[s1@subject s2@account s3@ref -], A>T2[s1@subject s2@account s3@ref -], T2>V2[s1@subject s2@account s3@ref -]⚑, V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-]` | block / allow | **GREEN** |  |
| [C5](cases/C5/result.md) | `trunk/one/leaf-to-leaf` | authored | `U>A[-], A>L[-], L>A[-], A>T1[-], T1>V1[-], V1>T1[s1 s2 s3 s4 -], T1>A[s1 s2 s3 -], A>L[s1@subject s2@account s3@ref -], L>A[s1@subject s2@account s3@ref -], A>T2[s1@subject s2@account s3@ref -], T2>V2[s1@subject s2@account s3@ref -]⚑, V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-]` | block / allow | **GREEN** |  |
| [C6](cases/C6/result.md) | `trunk/one/leaf-to-leaf (leaf invisible)` | authored | `U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2 s3 -], A>L[s1@subject s2@account s3@ref -], L>A[s1@subject s2@account s3@ref -], A>T2[s1@subject s2@account s3@ref -], T2>V2[s1@subject s2@account s3@ref -]⚑, V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-]` | block / allow | **GREEN** |  |
| [K0](cases/K0/result.md) | `trunk/cross` | real | `U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2 s3 -], A>L[s1 s2 s3 -], L>A[s1 s2 s3 -], A>T2[s1 s2 s3 -], T2>V2[s1 s2 s3 -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-] ~10min> U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>T3[-], T3>V3[-]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]` | allow / allow | **GREEN** |  |
| [K1](cases/K1/result.md) | `trunk/cross` | authored | `U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2 s3 -], A>L[s1 s2 s3 -], L>A[s1 s2 s3 -], A>T2[s1 s2 s3 -], T2>V2[s1 s2 s3 -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-] ~10min> U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[s1@subject s2@account s3@ref -], T2>A[s1@subject s2@account s3@ref -], A>L[s1@subject s2@account s3@ref -], L>A[s1@subject s2@account s3@ref -], A>T3[s1@subject s2@account s3@ref -], T3>V3[s1@subject s2@account s3@ref -]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]` | allow / allow | **PENDING** | pending: cross-conversation lineage not on this branch: the cross-trace fold (#273) with its seed rule and window, and the risk engine forwarding a cross-trace origin (labels.forwarded keeps same-trace origins only) |
| [K2](cases/K2/result.md) | `trunk/cross` | authored | `U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2 s3 -], A>L[s1 s2 s3 -], L>A[s1 s2 s3 -], A>T2[s1 s2 s3 -], T2>V2[s1 s2 s3 -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-] ~10min> U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[s1@subject -], T2>A[s1@subject -], A>L[s1@subject -], L>A[s1@subject -], A>T3[s1@subject -], T3>V3[s1@subject -]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]` | allow / allow | **PENDING** | pending: cross-conversation lineage not on this branch: the cross-trace fold (#273) with its seed rule and window, and the risk engine forwarding a cross-trace origin (labels.forwarded keeps same-trace origins only) |
| [K3](cases/K3/result.md) | `trunk/cross` | authored | `U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2 s3 -], A>L[s1 s2 s3 -], L>A[s1 s2 s3 -], A>T2[s1 s2 s3 -], T2>V2[s1 s2 s3 -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-] ~10min> U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[s2@account -], T2>A[s2@account -], A>L[s2@account -], L>A[s2@account -], A>T3[s2@account -], T3>V3[s2@account -]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]` | allow / allow | **PENDING** | pending: cross-conversation lineage not on this branch: the cross-trace fold (#273) with its seed rule and window, and the risk engine forwarding a cross-trace origin (labels.forwarded keeps same-trace origins only) |
| [K4](cases/K4/result.md) | `trunk/cross` | authored | `U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2 s3 -], A>L[s1 s2 s3 -], L>A[s1 s2 s3 -], A>T2[s1 s2 s3 -], T2>V2[s1 s2 s3 -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-] ~10min> U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[s3@ref -], T2>A[s3@ref -], A>L[s3@ref -], L>A[s3@ref -], A>T3[s3@ref -], T3>V3[s3@ref -]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]` | allow / allow | **GREEN** |  |
| [K5](cases/K5/result.md) | `trunk/cross` | authored | `U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2 s3 -], A>L[s1 s2 s3 -], L>A[s1 s2 s3 -], A>T2[s1 s2 s3 -], T2>V2[s1 s2 s3 -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-] ~10min> U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[s1@subject s2@account -], T2>A[s1@subject s2@account -], A>L[s1@subject s2@account -], L>A[s1@subject s2@account -], A>T3[s1@subject s2@account -], T3>V3[s1@subject s2@account -]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]` | allow / allow | **PENDING** | pending: cross-conversation lineage not on this branch: the cross-trace fold (#273) with its seed rule and window, and the risk engine forwarding a cross-trace origin (labels.forwarded keeps same-trace origins only) |
| [K6](cases/K6/result.md) | `trunk/cross` | authored | `U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2 s3 -], A>L[s1 s2 s3 -], L>A[s1 s2 s3 -], A>T2[s1 s2 s3 -], T2>V2[s1 s2 s3 -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-] ~10min> U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[s1@subject s3@ref -], T2>A[s1@subject s3@ref -], A>L[s1@subject s3@ref -], L>A[s1@subject s3@ref -], A>T3[s1@subject s3@ref -], T3>V3[s1@subject s3@ref -]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]` | allow / allow | **PENDING** | pending: cross-conversation lineage not on this branch: the cross-trace fold (#273) with its seed rule and window, and the risk engine forwarding a cross-trace origin (labels.forwarded keeps same-trace origins only) |
| [K7](cases/K7/result.md) | `trunk/cross` | authored | `U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2 s3 -], A>L[s1 s2 s3 -], L>A[s1 s2 s3 -], A>T2[s1 s2 s3 -], T2>V2[s1 s2 s3 -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-] ~10min> U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[s2@account s3@ref -], T2>A[s2@account s3@ref -], A>L[s2@account s3@ref -], L>A[s2@account s3@ref -], A>T3[s2@account s3@ref -], T3>V3[s2@account s3@ref -]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]` | allow / allow | **PENDING** | pending: cross-conversation lineage not on this branch: the cross-trace fold (#273) with its seed rule and window, and the risk engine forwarding a cross-trace origin (labels.forwarded keeps same-trace origins only) |
| [P1](cases/P1/result.md) | `trunk/control/one-call-two-partners` | authored | `U>A[s1 -], A>T1[s1 -], T1>V1[s1 -], V1>T1[s2 -], T1>V2[s1@subject s2@account -]⚑, V2>T1[-], T1>A[s1@subject s2@account -], A>U[-]` | block / allow | **GREEN** |  |
| [P2](cases/P2/result.md) | `trunk/control/two-callers-in-flight` | authored | `U>A1[s1 -], U>A2[s2 -], A1>T1[s1 -], A2>T1[s2 -], T1(A1)>V1[s1@subject -]⚑, V1>T1[-], T1(A2)>V2[s2@account -], V2>T1[-], T1>A1[s1@subject -], T1>A2[s2@account -], A1>U[-], A2>U[-]` | block / allow | **GREEN** |  |
| [P3](cases/P3/result.md) | `trunk/control/two-callers-one-value` | authored | `U>A1[s1 -], U>A2[s1 -], A1>T1[s1 -], A2>T1[s1 -], T1(A1)>V1[s1@subject -]⚑, V1>T1[-], T1(A2)>V2[s1@subject -], V2>T1[-], T1>A1[s1@subject -], T1>A2[s1@subject -], A1>U[-], A2>U[-]` | block / allow | **GREEN** |  |

**GREEN 11 · PENDING 6**

---

# C1 — quiet

cell `trunk/one/quiet`  ·  status **GREEN**

```
U>A[-], A>L[-], L>A[-], A>T1[-], T1>V1[-]⚑, V1>T1[-], T1>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-]
```

```
expected
              1      2      3      4      5      6      7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   T1>V1⚑ V1>T1  T1>A   A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

observed
              1      2      3      4      5      6      7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   T1>V1⚑ V1>T1  T1>A   A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
verdict at 5: expected on=allow / off=allow, observed on=allow / off=allow
```

**Rows (inherited labels on).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A | PUBLIC  | — | — |
| 2 | A>L | PUBLIC  | — | — |
| 3 | L>A | PUBLIC  | — | — |
| 4 | A>T1 | PUBLIC  | — | — |
| 5 | T1>V1⚑ | PUBLIC  | — | none allow [] v1 |
| 6 | V1>T1 | PUBLIC  | — | — |
| 7 | T1>A | PUBLIC  | — | — |
| 8 | A>L | PUBLIC  | — | — |
| 9 | L>A | PUBLIC  | — | — |
| 10 | A>T2 | PUBLIC  | — | — |
| 11 | T2>V2 | PUBLIC  | — | — |
| 12 | V2>T2 | PUBLIC  | — | — |
| 13 | T2>A | PUBLIC  | — | — |
| 14 | A>L | PUBLIC  | — | — |
| 15 | L>A | PUBLIC  | — | — |
| 16 | A>U | PUBLIC  | — | — |

**Timing (on).** total 1.5527 s: reset 0.1053, receiver 0.0601, interactions 0.9536, data_lineage 0.1978, classification 0.0167, label_lineage 0.0223, leg_ready+risk 0.1752, read 0.0217
OPA calls: 16

**Notes.** No route. Nothing sensitive is on the wire; the control for the rule and the harness.

---

# C2 — seen throughout

cell `trunk/one/root-to-left-leaf`  ·  status **GREEN**

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
              1      2      3      4      5      6      7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   T1>V1⚑ V1>T1  T1>A   A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
MRN     s2    ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ●      ━━━━━━●━━━━━━●━━━━━━●━━━━━━●·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ●      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
neighbourhood at 2: s1 2 of 3, s2 2 of 3, s3 2 of 3
neighbourhood at 3: s1 2 of 3, s2 2 of 3, s3 2 of 3
neighbourhood at 4: s1 2 of 3, s2 2 of 3, s3 2 of 3
neighbourhood at 5: s1 2 of 3, s2 2 of 3, s3 2 of 3
verdict at 5: expected on=block / off=block, observed on=block / off=block
```

**Rows (inherited labels on).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A | RESTRICTED DIS=type 2 diabetes DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | — | — |
| 2 | A>L | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 1), s2=4471820 (MRN, from 1), s1=123-45-6789 (SSN, from 1) | — |
| 3 | L>A | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 2), s2=4471820 (MRN, from 2), s1=123-45-6789 (SSN, from 2) | — |
| 4 | A>T1 | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 1), s2=4471820 (MRN, from 1), s1=123-45-6789 (SSN, from 1), s3=1961-03-14 (DOB, from 3), s2=4471820 (MRN, from 3), s1=123-45-6789 (SSN, from 3) | — |
| 5 | T1>V1⚑ | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 4), s2=4471820 (MRN, from 4), s1=123-45-6789 (SSN, from 4) | critical block ['T-SENS'] v1 |
| 6 | V1>T1 | PUBLIC  | — | — |
| 7 | T1>A | PUBLIC  | — | — |
| 8 | A>L | PUBLIC  | — | — |
| 9 | L>A | PUBLIC  | — | — |
| 10 | A>T2 | PUBLIC  | — | — |
| 11 | T2>V2 | PUBLIC  | — | — |
| 12 | V2>T2 | PUBLIC  | — | — |
| 13 | T2>A | PUBLIC  | — | — |
| 14 | A>L | PUBLIC  | — | — |
| 15 | L>A | PUBLIC  | — | — |
| 16 | A>U | PUBLIC  | — | — |

**Timing (on).** total 1.657 s: reset 0.0843, receiver 0.0574, interactions 0.9267, data_lineage 0.2092, classification 0.0243, label_lineage 0.037, leg_ready+risk 0.3026, read 0.0155
OPA calls: 19

**Notes.** Root to the left leaf, U>A>T1>V1. The classifier labels the three on every leg; lineage draws the chain, the classifier alone already decides.

---

# C3 — lost after the prompt

cell `trunk/one/root-to-left-leaf`  ·  status **GREEN**  ·  classifier **authored**: the model's findings subtracted at legs [3, 4, 5]

```
U>A[s1 s2 s3 s4 -], A>L[s1 s2 s3 -], L>A[s1@subject s2@account s3@ref -], A>T1[s1@subject s2@account s3@ref -], T1>V1[s1@subject s2@account s3@ref -]⚑, V1>T1[-], T1>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-]
```

```
expected
              1      2      3      4      5      6      7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   T1>V1⚑ V1>T1  T1>A   A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ●      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
MRN     s2    ●      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ●      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ●      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

observed
              1      2      3      4      5      6      7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   T1>V1⚑ V1>T1  T1>A   A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ●      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
MRN     s2    ●      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ●      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ●      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
neighbourhood at 2: s1 2 of 3, s2 2 of 3, s3 2 of 3
neighbourhood at 3: s1 2 of 3, s2 2 of 3, s3 2 of 3
neighbourhood at 4: s1 2 of 3, s2 2 of 3, s3 2 of 3
neighbourhood at 5: s1 2 of 3, s2 2 of 3, s3 2 of 3
verdict at 5: expected on=block / off=allow, observed on=block / off=allow
```

**Rows (inherited labels on).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A | RESTRICTED DIS=type 2 diabetes DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | — | — |
| 2 | A>L | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 1), s2=4471820 (MRN, from 1), s1=123-45-6789 (SSN, from 1) | — |
| 3 | L>A | PUBLIC  | s3=1961-03-14 (DOB, from 2), s2=4471820 (MRN, from 2), s1=123-45-6789 (SSN, from 2) | — |
| 4 | A>T1 | PUBLIC  | s3=1961-03-14 (DOB, from 1), s2=4471820 (MRN, from 1), s1=123-45-6789 (SSN, from 1), s3=1961-03-14 (DOB, from 3), s2=4471820 (MRN, from 3), s1=123-45-6789 (SSN, from 3) | — |
| 5 | T1>V1⚑ | PUBLIC  | s3=1961-03-14 (DOB, from 4), s2=4471820 (MRN, from 4), s1=123-45-6789 (SSN, from 4) | critical block ['T-SENS'] v1 |
| 6 | V1>T1 | PUBLIC  | — | — |
| 7 | T1>A | PUBLIC  | — | — |
| 8 | A>L | PUBLIC  | — | — |
| 9 | L>A | PUBLIC  | — | — |
| 10 | A>T2 | PUBLIC  | — | — |
| 11 | T2>V2 | PUBLIC  | — | — |
| 12 | V2>T2 | PUBLIC  | — | — |
| 13 | T2>A | PUBLIC  | — | — |
| 14 | A>L | PUBLIC  | — | — |
| 15 | L>A | PUBLIC  | — | — |
| 16 | A>U | PUBLIC  | — | — |

**Timing (on).** total 1.4588 s: reset 0.0881, receiver 0.0587, interactions 0.8401, data_lineage 0.165, classification 0.0252, label_lineage 0.0312, leg_ready+risk 0.2366, read 0.0139
OPA calls: 19

**Notes.** Same route as C2. From the LLM's answer on the facts travel under keys the model does not recognise; only lineage carries the label to V1.

---

# C4 — held for the second vendor

cell `trunk/one/root-to-right-leaf`  ·  status **GREEN**  ·  classifier **authored**: the model's findings subtracted at legs [9, 10, 11]

```
U>A[s1 s2 s3 s4 -], A>L[-], L>A[-], A>T1[-], T1>V1[-], V1>T1[-], T1>A[-], A>L[s1 s2 s3 -], L>A[s1@subject s2@account s3@ref -], A>T2[s1@subject s2@account s3@ref -], T2>V2[s1@subject s2@account s3@ref -]⚑, V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-]
```

```
expected
              1      2      3      4      5      6      7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   T1>V1  V1>T1  T1>A   A>L    L>A    A>T2   T2>V2⚑ V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ●      ·      ·      ·      ·      ·      ·      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
MRN     s2    ●      ·      ·      ·      ·      ·      ·      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DOB     s3    ●      ·      ·      ·      ·      ·      ·      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DIS     s4    ●      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

observed
              1      2      3      4      5      6      7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   T1>V1  V1>T1  T1>A   A>L    L>A    A>T2   T2>V2⚑ V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ●      ·      ·      ·      ·      ·      ·      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
MRN     s2    ●      ·      ·      ·      ·      ·      ·      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DOB     s3    ●      ·      ·      ·      ·      ·      ·      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DIS     s4    ●      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
neighbourhood at 8: s1 2 of 3, s2 2 of 3, s3 2 of 3
neighbourhood at 9: s1 2 of 3, s2 2 of 3, s3 2 of 3
neighbourhood at 10: s1 2 of 3, s2 2 of 3, s3 2 of 3
neighbourhood at 11: s1 2 of 3, s2 2 of 3, s3 2 of 3
verdict at 11: expected on=block / off=allow, observed on=block / off=allow
```

**Rows (inherited labels on).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A | RESTRICTED DIS=type 2 diabetes DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | — | — |
| 2 | A>L | PUBLIC  | — | — |
| 3 | L>A | PUBLIC  | — | — |
| 4 | A>T1 | PUBLIC  | — | — |
| 5 | T1>V1 | PUBLIC  | — | — |
| 6 | V1>T1 | PUBLIC  | — | — |
| 7 | T1>A | PUBLIC  | — | — |
| 8 | A>L | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 1), s2=4471820 (MRN, from 1), s1=123-45-6789 (SSN, from 1) | — |
| 9 | L>A | PUBLIC  | s3=1961-03-14 (DOB, from 8), s2=4471820 (MRN, from 8), s1=123-45-6789 (SSN, from 8) | — |
| 10 | A>T2 | PUBLIC  | s3=1961-03-14 (DOB, from 1), s2=4471820 (MRN, from 1), s1=123-45-6789 (SSN, from 1), s3=1961-03-14 (DOB, from 9), s2=4471820 (MRN, from 9), s1=123-45-6789 (SSN, from 9) | — |
| 11 | T2>V2⚑ | PUBLIC  | s3=1961-03-14 (DOB, from 10), s2=4471820 (MRN, from 10), s1=123-45-6789 (SSN, from 10) | critical block ['T-SENS'] v1 |
| 12 | V2>T2 | PUBLIC  | — | — |
| 13 | T2>A | PUBLIC  | — | — |
| 14 | A>L | PUBLIC  | — | — |
| 15 | L>A | PUBLIC  | — | — |
| 16 | A>U | PUBLIC  | — | — |

**Timing (on).** total 2.2131 s: reset 0.1279, receiver 0.0809, interactions 1.3356, data_lineage 0.254, classification 0.0341, label_lineage 0.0225, leg_ready+risk 0.3384, read 0.0197
OPA calls: 19

**Notes.** Root to the right leaf, the left branch benign: legs 4 to 7 carry nothing, the agent held the facts across a clean exchange.

---

# C5 — returned by the first vendor

cell `trunk/one/leaf-to-leaf`  ·  status **GREEN**  ·  classifier **authored**: the model's findings subtracted at legs [8, 9, 10, 11]

```
U>A[-], A>L[-], L>A[-], A>T1[-], T1>V1[-], V1>T1[s1 s2 s3 s4 -], T1>A[s1 s2 s3 -], A>L[s1@subject s2@account s3@ref -], L>A[s1@subject s2@account s3@ref -], A>T2[s1@subject s2@account s3@ref -], T2>V2[s1@subject s2@account s3@ref -]⚑, V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-]
```

```
expected
              1      2      3      4      5      6      7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   T1>V1  V1>T1  T1>A   A>L    L>A    A>T2   T2>V2⚑ V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ●      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ●      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ●      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ●      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

observed
              1      2      3      4      5      6      7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   T1>V1  V1>T1  T1>A   A>L    L>A    A>T2   T2>V2⚑ V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ●      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ●      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ●      ━━━━━━●━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ●      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
neighbourhood at 7: s1 2 of 3, s2 2 of 3, s3 2 of 3
neighbourhood at 8: s1 2 of 3, s2 2 of 3, s3 2 of 3
neighbourhood at 9: s1 2 of 3, s2 2 of 3, s3 2 of 3
neighbourhood at 10: s1 2 of 3, s2 2 of 3, s3 2 of 3
neighbourhood at 11: s1 2 of 3, s2 2 of 3, s3 2 of 3
verdict at 11: expected on=block / off=allow, observed on=block / off=allow
```

**Rows (inherited labels on).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A | PUBLIC  | — | — |
| 2 | A>L | PUBLIC  | — | — |
| 3 | L>A | PUBLIC  | — | — |
| 4 | A>T1 | PUBLIC  | — | — |
| 5 | T1>V1 | PUBLIC  | — | — |
| 6 | V1>T1 | RESTRICTED DIS=type 2 diabetes DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | — | — |
| 7 | T1>A | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | s3=1961-03-14 (DOB, from 6), s2=4471820 (MRN, from 6), s1=123-45-6789 (SSN, from 6) | — |
| 8 | A>L | PUBLIC  | s3=1961-03-14 (DOB, from 7), s2=4471820 (MRN, from 7), s1=123-45-6789 (SSN, from 7) | — |
| 9 | L>A | PUBLIC  | s3=1961-03-14 (DOB, from 8), s2=4471820 (MRN, from 8), s1=123-45-6789 (SSN, from 8) | — |
| 10 | A>T2 | PUBLIC  | s3=1961-03-14 (DOB, from 7), s2=4471820 (MRN, from 7), s1=123-45-6789 (SSN, from 7), s3=1961-03-14 (DOB, from 9), s2=4471820 (MRN, from 9), s1=123-45-6789 (SSN, from 9) | — |
| 11 | T2>V2⚑ | PUBLIC  | s3=1961-03-14 (DOB, from 10), s2=4471820 (MRN, from 10), s1=123-45-6789 (SSN, from 10) | critical block ['T-SENS'] v1 |
| 12 | V2>T2 | PUBLIC  | — | — |
| 13 | T2>A | PUBLIC  | — | — |
| 14 | A>L | PUBLIC  | — | — |
| 15 | L>A | PUBLIC  | — | — |
| 16 | A>U | PUBLIC  | — | — |

**Timing (on).** total 1.8849 s: reset 0.1199, receiver 0.0822, interactions 1.1476, data_lineage 0.2043, classification 0.0344, label_lineage 0.0286, leg_ready+risk 0.2502, read 0.0177
OPA calls: 20

**Notes.** Leaf to leaf, starting from a response: born in the insurer's answer. T1 forgets, so leg 7 inherits leg 6 only through the invocation-scope fix of #348 (a response inherits what its child calls returned).

---

# C6 — returned unseen

cell `trunk/one/leaf-to-leaf (leaf invisible)`  ·  status **GREEN**  ·  classifier **authored**: the model's findings subtracted at legs [8, 9, 10, 11]

```
U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2 s3 -], A>L[s1@subject s2@account s3@ref -], L>A[s1@subject s2@account s3@ref -], A>T2[s1@subject s2@account s3@ref -], T2>V2[s1@subject s2@account s3@ref -]⚑, V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-]
```

```
expected
              1      2      3      4      5-6    7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   ..V1.. T1>A   A>L    L>A    A>T2   T2>V2⚑ V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·             ●      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·             ●      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·             ●      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·             ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

observed
              1      2      3      4      5-6    7      8      9      10     11     12     13     14     15     16     
              U>A    A>L    L>A    A>T1   ..V1.. T1>A   A>L    L>A    A>T2   T2>V2⚑ V2>T2  T2>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·             ●      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·             ●      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·             ●      ━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·             ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
neighbourhood at 8: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 9: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 10: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 11: s1 2 of 2, s2 2 of 2, s3 2 of 2
verdict at 11: expected on=block / off=allow, observed on=block / off=allow
```

**Rows (inherited labels on).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A | PUBLIC  | — | — |
| 2 | A>L | PUBLIC  | — | — |
| 3 | L>A | PUBLIC  | — | — |
| 4 | A>T1 | PUBLIC  | — | — |
| 5 | ..V1.. | — | — | — |
| 7 | T1>A | RESTRICTED DOB=1961-03-14 MRN=4471820 SSN=123-45-6789 | — | — |
| 8 | A>L | PUBLIC  | s3=1961-03-14 (DOB, from 7), s2=4471820 (MRN, from 7), s1=123-45-6789 (SSN, from 7) | — |
| 9 | L>A | PUBLIC  | s3=1961-03-14 (DOB, from 8), s2=4471820 (MRN, from 8), s1=123-45-6789 (SSN, from 8) | — |
| 10 | A>T2 | PUBLIC  | s3=1961-03-14 (DOB, from 7), s2=4471820 (MRN, from 7), s1=123-45-6789 (SSN, from 7), s3=1961-03-14 (DOB, from 9), s2=4471820 (MRN, from 9), s1=123-45-6789 (SSN, from 9) | — |
| 11 | T2>V2⚑ | PUBLIC  | s3=1961-03-14 (DOB, from 10), s2=4471820 (MRN, from 10), s1=123-45-6789 (SSN, from 10) | critical block ['T-SENS'] v1 |
| 12 | V2>T2 | PUBLIC  | — | — |
| 13 | T2>A | PUBLIC  | — | — |
| 14 | A>L | PUBLIC  | — | — |
| 15 | L>A | PUBLIC  | — | — |
| 16 | A>U | PUBLIC  | — | — |

**Timing (on).** total 1.5183 s: reset 0.1289, receiver 0.0751, interactions 0.9129, data_lineage 0.1502, classification 0.022, label_lineage 0.0205, leg_ready+risk 0.1885, read 0.0202
OPA calls: 17

**Notes.** C5 with V1 off the wire: T1 is the leaf, the origin is T1's answer at leg 7; legs 5 and 6 are the unseen hop.

---

# K0 — reborn, nothing

cell `trunk/cross`  ·  status **GREEN**

```
U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2 s3 -], A>L[s1 s2 s3 -], L>A[s1 s2 s3 -], A>T2[s1 s2 s3 -], T2>V2[s1 s2 s3 -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-] ~10min> U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>T3[-], T3>V3[-]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]
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
SSN     s1    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
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
              U>A    A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>T3   T3>V3⚑ V3>T3  T3>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
neighbourhood at 8: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 9: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 10: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 11: s1 2 of 2, s2 2 of 2, s3 2 of 2
verdict at 27: expected on=allow / off=allow, observed on=allow / off=allow
```

**Rows (inherited labels on).**

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
| 27 | T3>V3⚑ | PUBLIC  | — | none allow [] v1 |
| 28 | V3>T3 | PUBLIC  | — | — |
| 29 | T3>A | PUBLIC  | — | — |
| 30 | A>L | PUBLIC  | — | — |
| 31 | L>A | PUBLIC  | — | — |
| 32 | A>U | PUBLIC  | — | — |

**Timing (on).** total 2.5815 s: reset 0.0928, receiver 0.088, interactions 1.6241, data_lineage 0.3287, classification 0.0274, label_lineage 0.0311, leg_ready+risk 0.3739, read 0.0155
OPA calls: 33

**Notes.** Conversation 2 is C5's shape; V2 returns nothing (neighbourhood —). The classifier labels nothing in conversation 2, so only a crossing from conversation 1 can produce the alert at V3. Wanted under person-anchored: allow.

---

# K1 — reborn, all three

cell `trunk/cross`  ·  status **PENDING**  ·  classifier **authored**: the model's findings subtracted at legs [22, 23, 24, 25, 26, 27]

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
SSN     s1    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
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
              U>A    A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>T3   T3>V3⚑ V3>T3  T3>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
neighbourhood at 8: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 9: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 10: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 11: s1 2 of 2, s2 2 of 2, s3 2 of 2
verdict at 27: expected on=block / off=allow, observed on=allow / off=allow
```

**Reasons.** pending: cross-conversation lineage not on this branch: the cross-trace fold (#273) with its seed rule and window, and the risk engine forwarding a cross-trace origin (labels.forwarded keeps same-trace origins only) · verdict on: want block, got allow · edges missing: s1 11>23, s1 22>23, s1 23>24, s1 24>25, s1 25>26, s1 26>27, s2 11>23, s2 22>23, s2 23>24, s2 24>25, s2 25>26, s2 26>27, s3 11>23, s3 22>23, s3 23>24, s3 24>25, s3 25>26, s3 26>27

**Rows (inherited labels on).**

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
| 27 | T3>V3⚑ | PUBLIC  | — | none allow [] v1 |
| 28 | V3>T3 | PUBLIC  | — | — |
| 29 | T3>A | PUBLIC  | — | — |
| 30 | A>L | PUBLIC  | — | — |
| 31 | L>A | PUBLIC  | — | — |
| 32 | A>U | PUBLIC  | — | — |

**Timing (on).** total 2.8351 s: reset 0.0788, receiver 0.095, interactions 1.8567, data_lineage 0.3923, classification 0.0417, label_lineage 0.0298, leg_ready+risk 0.3264, read 0.0144
OPA calls: 33

**Notes.** Conversation 2 is C5's shape; V2 returns s1 s2 s3 (neighbourhood 2 of 2). The classifier labels nothing in conversation 2, so only a crossing from conversation 1 can produce the alert at V3. Wanted under person-anchored: block.

---

# K2 — reborn, the SSN alone

cell `trunk/cross`  ·  status **PENDING**  ·  classifier **authored**: the model's findings subtracted at legs [22, 23, 24, 25, 26, 27]

```
U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2 s3 -], A>L[s1 s2 s3 -], L>A[s1 s2 s3 -], A>T2[s1 s2 s3 -], T2>V2[s1 s2 s3 -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-] ~10min> U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[s1@subject -], T2>A[s1@subject -], A>L[s1@subject -], L>A[s1@subject -], A>T3[s1@subject -], T3>V3[s1@subject -]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]
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
SSN     s1    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
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
              U>A    A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>T3   T3>V3⚑ V3>T3  T3>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
neighbourhood at 8: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 9: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 10: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 11: s1 2 of 2, s2 2 of 2, s3 2 of 2
verdict at 27: expected on=block / off=allow, observed on=allow / off=allow
```

**Reasons.** pending: cross-conversation lineage not on this branch: the cross-trace fold (#273) with its seed rule and window, and the risk engine forwarding a cross-trace origin (labels.forwarded keeps same-trace origins only) · verdict on: want block, got allow · edges missing: s1 11>23, s1 22>23, s1 23>24, s1 24>25, s1 25>26, s1 26>27

**Rows (inherited labels on).**

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
| 27 | T3>V3⚑ | PUBLIC  | — | none allow [] v1 |
| 28 | V3>T3 | PUBLIC  | — | — |
| 29 | T3>A | PUBLIC  | — | — |
| 30 | A>L | PUBLIC  | — | — |
| 31 | L>A | PUBLIC  | — | — |
| 32 | A>U | PUBLIC  | — | — |

**Timing (on).** total 2.8133 s: reset 0.1286, receiver 0.1043, interactions 1.7108, data_lineage 0.412, classification 0.0394, label_lineage 0.0283, leg_ready+risk 0.3727, read 0.0172
OPA calls: 33

**Notes.** Conversation 2 is C5's shape; V2 returns s1 (neighbourhood 0 of 2). The classifier labels nothing in conversation 2, so only a crossing from conversation 1 can produce the alert at V3. Wanted under person-anchored: block.

---

# K3 — reborn, the MRN alone

cell `trunk/cross`  ·  status **PENDING**  ·  classifier **authored**: the model's findings subtracted at legs [22, 23, 24, 25, 26, 27]

```
U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2 s3 -], A>L[s1 s2 s3 -], L>A[s1 s2 s3 -], A>T2[s1 s2 s3 -], T2>V2[s1 s2 s3 -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-] ~10min> U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[s2@account -], T2>A[s2@account -], A>L[s2@account -], L>A[s2@account -], A>T3[s2@account -], T3>V3[s2@account -]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]
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
SSN     s1    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
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
              U>A    A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>T3   T3>V3⚑ V3>T3  T3>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
neighbourhood at 8: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 9: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 10: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 11: s1 2 of 2, s2 2 of 2, s3 2 of 2
verdict at 27: expected on=block / off=allow, observed on=allow / off=allow
```

**Reasons.** pending: cross-conversation lineage not on this branch: the cross-trace fold (#273) with its seed rule and window, and the risk engine forwarding a cross-trace origin (labels.forwarded keeps same-trace origins only) · verdict on: want block, got allow · edges missing: s2 11>23, s2 22>23, s2 23>24, s2 24>25, s2 25>26, s2 26>27

**Rows (inherited labels on).**

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
| 27 | T3>V3⚑ | PUBLIC  | — | none allow [] v1 |
| 28 | V3>T3 | PUBLIC  | — | — |
| 29 | T3>A | PUBLIC  | — | — |
| 30 | A>L | PUBLIC  | — | — |
| 31 | L>A | PUBLIC  | — | — |
| 32 | A>U | PUBLIC  | — | — |

**Timing (on).** total 2.5251 s: reset 0.0749, receiver 0.0829, interactions 1.5336, data_lineage 0.3466, classification 0.0432, label_lineage 0.027, leg_ready+risk 0.4006, read 0.0163
OPA calls: 33

**Notes.** Conversation 2 is C5's shape; V2 returns s2 (neighbourhood 0 of 2). The classifier labels nothing in conversation 2, so only a crossing from conversation 1 can produce the alert at V3. Wanted under person-anchored: block.

---

# K4 — reborn, the birthdate alone

cell `trunk/cross`  ·  status **GREEN**  ·  classifier **authored**: the model's findings subtracted at legs [22, 23, 24, 25, 26, 27]

```
U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2 s3 -], A>L[s1 s2 s3 -], L>A[s1 s2 s3 -], A>T2[s1 s2 s3 -], T2>V2[s1 s2 s3 -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-] ~10min> U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[s3@ref -], T2>A[s3@ref -], A>L[s3@ref -], L>A[s3@ref -], A>T3[s3@ref -], T3>V3[s3@ref -]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]
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
SSN     s1    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
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
              U>A    A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>T3   T3>V3⚑ V3>T3  T3>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
neighbourhood at 8: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 9: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 10: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 11: s1 2 of 2, s2 2 of 2, s3 2 of 2
verdict at 27: expected on=allow / off=allow, observed on=allow / off=allow
```

**Rows (inherited labels on).**

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
| 27 | T3>V3⚑ | PUBLIC  | — | none allow [] v1 |
| 28 | V3>T3 | PUBLIC  | — | — |
| 29 | T3>A | PUBLIC  | — | — |
| 30 | A>L | PUBLIC  | — | — |
| 31 | L>A | PUBLIC  | — | — |
| 32 | A>U | PUBLIC  | — | — |

**Timing (on).** total 2.5161 s: reset 0.0925, receiver 0.0818, interactions 1.4998, data_lineage 0.3736, classification 0.0362, label_lineage 0.028, leg_ready+risk 0.3842, read 0.02
OPA calls: 33

**Notes.** Conversation 2 is C5's shape; V2 returns s3 (neighbourhood 0 of 2). The classifier labels nothing in conversation 2, so only a crossing from conversation 1 can produce the alert at V3. Wanted under person-anchored: allow.

---

# K5 — reborn, SSN and MRN

cell `trunk/cross`  ·  status **PENDING**  ·  classifier **authored**: the model's findings subtracted at legs [22, 23, 24, 25, 26, 27]

```
U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2 s3 -], A>L[s1 s2 s3 -], L>A[s1 s2 s3 -], A>T2[s1 s2 s3 -], T2>V2[s1 s2 s3 -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-] ~10min> U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[s1@subject s2@account -], T2>A[s1@subject s2@account -], A>L[s1@subject s2@account -], L>A[s1@subject s2@account -], A>T3[s1@subject s2@account -], T3>V3[s1@subject s2@account -]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]
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
SSN     s1    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
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
              U>A    A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>T3   T3>V3⚑ V3>T3  T3>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
neighbourhood at 8: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 9: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 10: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 11: s1 2 of 2, s2 2 of 2, s3 2 of 2
verdict at 27: expected on=block / off=allow, observed on=allow / off=allow
```

**Reasons.** pending: cross-conversation lineage not on this branch: the cross-trace fold (#273) with its seed rule and window, and the risk engine forwarding a cross-trace origin (labels.forwarded keeps same-trace origins only) · verdict on: want block, got allow · edges missing: s1 11>23, s1 22>23, s1 23>24, s1 24>25, s1 25>26, s1 26>27, s2 11>23, s2 22>23, s2 23>24, s2 24>25, s2 25>26, s2 26>27

**Rows (inherited labels on).**

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
| 27 | T3>V3⚑ | PUBLIC  | — | none allow [] v1 |
| 28 | V3>T3 | PUBLIC  | — | — |
| 29 | T3>A | PUBLIC  | — | — |
| 30 | A>L | PUBLIC  | — | — |
| 31 | L>A | PUBLIC  | — | — |
| 32 | A>U | PUBLIC  | — | — |

**Timing (on).** total 2.4053 s: reset 0.0914, receiver 0.0925, interactions 1.49, data_lineage 0.2849, classification 0.0366, label_lineage 0.0261, leg_ready+risk 0.3677, read 0.0161
OPA calls: 33

**Notes.** Conversation 2 is C5's shape; V2 returns s1 s2 (neighbourhood 1 of 2). The classifier labels nothing in conversation 2, so only a crossing from conversation 1 can produce the alert at V3. Wanted under person-anchored: block.

---

# K6 — reborn, SSN and birthdate

cell `trunk/cross`  ·  status **PENDING**  ·  classifier **authored**: the model's findings subtracted at legs [22, 23, 24, 25, 26, 27]

```
U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2 s3 -], A>L[s1 s2 s3 -], L>A[s1 s2 s3 -], A>T2[s1 s2 s3 -], T2>V2[s1 s2 s3 -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-] ~10min> U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[s1@subject s3@ref -], T2>A[s1@subject s3@ref -], A>L[s1@subject s3@ref -], L>A[s1@subject s3@ref -], A>T3[s1@subject s3@ref -], T3>V3[s1@subject s3@ref -]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]
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
SSN     s1    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
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
              U>A    A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>T3   T3>V3⚑ V3>T3  T3>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
neighbourhood at 8: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 9: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 10: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 11: s1 2 of 2, s2 2 of 2, s3 2 of 2
verdict at 27: expected on=block / off=allow, observed on=allow / off=allow
```

**Reasons.** pending: cross-conversation lineage not on this branch: the cross-trace fold (#273) with its seed rule and window, and the risk engine forwarding a cross-trace origin (labels.forwarded keeps same-trace origins only) · verdict on: want block, got allow · edges missing: s1 11>23, s1 22>23, s1 23>24, s1 24>25, s1 25>26, s1 26>27, s3 11>23, s3 22>23, s3 23>24, s3 24>25, s3 25>26, s3 26>27

**Rows (inherited labels on).**

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
| 27 | T3>V3⚑ | PUBLIC  | — | none allow [] v1 |
| 28 | V3>T3 | PUBLIC  | — | — |
| 29 | T3>A | PUBLIC  | — | — |
| 30 | A>L | PUBLIC  | — | — |
| 31 | L>A | PUBLIC  | — | — |
| 32 | A>U | PUBLIC  | — | — |

**Timing (on).** total 2.4494 s: reset 0.1031, receiver 0.0844, interactions 1.5686, data_lineage 0.2675, classification 0.045, label_lineage 0.0272, leg_ready+risk 0.3348, read 0.0188
OPA calls: 33

**Notes.** Conversation 2 is C5's shape; V2 returns s1 s3 (neighbourhood 1 of 2). The classifier labels nothing in conversation 2, so only a crossing from conversation 1 can produce the alert at V3. Wanted under person-anchored: block.

---

# K7 — reborn, MRN and birthdate

cell `trunk/cross`  ·  status **PENDING**  ·  classifier **authored**: the model's findings subtracted at legs [22, 23, 24, 25, 26, 27]

```
U>A[-], A>L[-], L>A[-], A>T1[-], ..V1.., T1>A[s1 s2 s3 -], A>L[s1 s2 s3 -], L>A[s1 s2 s3 -], A>T2[s1 s2 s3 -], T2>V2[s1 s2 s3 -], V2>T2[-], T2>A[-], A>L[-], L>A[-], A>U[-] ~10min> U>A[-], A>L[-], L>A[-], A>T2[-], T2>V2[-], V2>T2[s2@account s3@ref -], T2>A[s2@account s3@ref -], A>L[s2@account s3@ref -], L>A[s2@account s3@ref -], A>T3[s2@account s3@ref -], T3>V3[s2@account s3@ref -]⚑, V3>T3[-], T3>A[-], A>L[-], L>A[-], A>U[-]
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
SSN     s1    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○━━━━━━○━━━━━━○━━━━━━○━━━━━━○━━━━━━○·      ·      ·      ·      ·      
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
              U>A    A>L    L>A    A>T2   T2>V2  V2>T2  T2>A   A>L    L>A    A>T3   T3>V3⚑ V3>T3  T3>A   A>L    L>A    A>U    
SSN     s1    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
MRN     s2    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○┈┈┈┈┈┈○·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
neighbourhood at 8: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 9: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 10: s1 2 of 2, s2 2 of 2, s3 2 of 2
neighbourhood at 11: s1 2 of 2, s2 2 of 2, s3 2 of 2
verdict at 27: expected on=block / off=allow, observed on=allow / off=allow
```

**Reasons.** pending: cross-conversation lineage not on this branch: the cross-trace fold (#273) with its seed rule and window, and the risk engine forwarding a cross-trace origin (labels.forwarded keeps same-trace origins only) · verdict on: want block, got allow · edges missing: s2 11>23, s2 22>23, s2 23>24, s2 24>25, s2 25>26, s2 26>27, s3 11>23, s3 22>23, s3 23>24, s3 24>25, s3 25>26, s3 26>27

**Rows (inherited labels on).**

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
| 27 | T3>V3⚑ | PUBLIC  | — | none allow [] v1 |
| 28 | V3>T3 | PUBLIC  | — | — |
| 29 | T3>A | PUBLIC  | — | — |
| 30 | A>L | PUBLIC  | — | — |
| 31 | L>A | PUBLIC  | — | — |
| 32 | A>U | PUBLIC  | — | — |

**Timing (on).** total 2.3662 s: reset 0.0773, receiver 0.0904, interactions 1.5068, data_lineage 0.2777, classification 0.0372, label_lineage 0.0257, leg_ready+risk 0.3356, read 0.0155
OPA calls: 33

**Notes.** Conversation 2 is C5's shape; V2 returns s2 s3 (neighbourhood 1 of 2). The classifier labels nothing in conversation 2, so only a crossing from conversation 1 can produce the alert at V3. Wanted under person-anchored: block.

---

# P1 — one call, two partners

cell `trunk/control/one-call-two-partners`  ·  status **GREEN**  ·  classifier **authored**: the model's findings subtracted at legs [5, 7]

```
U>A[s1 -], A>T1[s1 -], T1>V1[s1 -], V1>T1[s2 -], T1>V2[s1@subject s2@account -]⚑, V2>T1[-], T1>A[s1@subject s2@account -], A>U[-]
```

```
expected
              1           2           3           4           5           6           7           8           
              U>A         A>T1        T1>V1       V1>T1       T1>V2⚑      V2>T1       T1>A        A>U         
SSN     s1    ●           ━━━━━━━━━━━●━━━━━━━━━━━●·           ━━━━━━━━━━━○·           ━━━━━━━━━━━○·           
MRN     s2    ·           ·           ·           ●           ━━━━━━━━━━━○·           ━━━━━━━━━━━○·           
DOB     s3    ·           ·           ·           ·           ·           ·           ·           ·           
DIS     s4    ·           ·           ·           ·           ·           ·           ·           ·           

observed
              1           2           3           4           5           6           7           8           
              U>A         A>T1        T1>V1       V1>T1       T1>V2⚑      V2>T1       T1>A        A>U         
SSN     s1    ●           ━━━━━━━━━━━●━━━━━━━━━━━●·           ━━━━━━━━━━━○·           ━━━━━━━━━━━○·           
MRN     s2    ·           ·           ·           ●           ━━━━━━━━━━━○·           ━━━━━━━━━━━○·           
DOB     s3    ·           ·           ·           ·           ·           ·           ·           ·           
DIS     s4    ·           ·           ·           ·           ·           ·           ·           ·           
neighbourhood at 2: s1 0 of 0
neighbourhood at 3: s1 0 of 0
neighbourhood at 5: s1 0 of 0, s2 0 of 0
neighbourhood at 7: s1 0 of 0, s2 0 of 0
verdict at 5: expected on=block / off=allow, observed on=block / off=allow
```

**Rows (inherited labels on).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A | RESTRICTED SSN=123-45-6789 | — | — |
| 2 | A>T1 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 1) | — |
| 3 | T1>V1 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 2) | — |
| 4 | V1>T1 | RESTRICTED MRN=4471820 | — | — |
| 5 | T1>V2⚑ | PUBLIC  | s1=123-45-6789 (SSN, from 2), s2=4471820 (MRN, from 4) | critical block ['T-SENS'] v1 |
| 6 | V2>T1 | PUBLIC  | — | — |
| 7 | T1>A | PUBLIC  | s1=123-45-6789 (SSN, from 2), s2=4471820 (MRN, from 4) | — |
| 8 | A>U | PUBLIC  | — | — |

**Timing (on).** total 0.7273 s: reset 0.1381, receiver 0.0417, interactions 0.2993, data_lineage 0.0733, classification 0.0185, label_lineage 0.0175, leg_ready+risk 0.1227, read 0.0162
OPA calls: 11

**Notes.** Picture 1 of #348. Inside A's call, T1 asks V1, then asks V2 with what V1 returned, then answers A. T1's second request inherits from the call that opened it (s1 2>5) and from V1's answer inside the call (s2 4>5); its answer carries both (2>7, 4>7). Before #348 the fold drew s2 4>5 only, and no 4>7.

---

# P2 — two callers in flight

cell `trunk/control/two-callers-in-flight`  ·  status **GREEN**  ·  classifier **authored**: the model's findings subtracted at legs [5, 7, 9, 10]

```
U>A1[s1 -], U>A2[s2 -], A1>T1[s1 -], A2>T1[s2 -], T1(A1)>V1[s1@subject -]⚑, V1>T1[-], T1(A2)>V2[s2@account -], V2>T1[-], T1>A1[s1@subject -], T1>A2[s2@account -], A1>U[-], A2>U[-]
```

```
expected
              1      2      3      4      5      6      7      8      9      10     11     12     
              U>A1   U>A2   A1>T1  A2>T1  T1>V1⚑ V1>T1  T1>V2  V2>T1  T1>A1  T1>A2  A1>U   A2>U   
SSN     s1    ●      ·      ━━━━━━●·      ━━━━━━○·      ·      ·      ━━━━━━○·      ·      ·      
MRN     s2    ·      ●      ·      ━━━━━━●·      ·      ━━━━━━○·      ·      ━━━━━━○·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

observed
              1      2      3      4      5      6      7      8      9      10     11     12     
              U>A1   U>A2   A1>T1  A2>T1  T1>V1⚑ V1>T1  T1>V2  V2>T1  T1>A1  T1>A2  A1>U   A2>U   
SSN     s1    ●      ·      ━━━━━━●·      ━━━━━━○·      ·      ·      ━━━━━━○·      ·      ·      
MRN     s2    ·      ●      ·      ━━━━━━●·      ·      ━━━━━━○·      ·      ━━━━━━○·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
neighbourhood at 3: s1 0 of 0
neighbourhood at 4: s2 0 of 0
neighbourhood at 5: s1 0 of 0
neighbourhood at 7: s2 0 of 0
neighbourhood at 9: s1 0 of 0
neighbourhood at 10: s2 0 of 0
verdict at 5: expected on=block / off=allow, observed on=block / off=allow
```

**Rows (inherited labels on).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A1 | RESTRICTED SSN=123-45-6789 | — | — |
| 2 | U>A2 | RESTRICTED MRN=4471820 | — | — |
| 3 | A1>T1 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 1) | — |
| 4 | A2>T1 | RESTRICTED MRN=4471820 | s2=4471820 (MRN, from 2) | — |
| 5 | T1>V1⚑ | PUBLIC  | s1=123-45-6789 (SSN, from 3) | critical block ['T-SENS'] v1 |
| 6 | V1>T1 | PUBLIC  | — | — |
| 7 | T1>V2 | PUBLIC  | s2=4471820 (MRN, from 4) | — |
| 8 | V2>T1 | PUBLIC  | — | — |
| 9 | T1>A1 | PUBLIC  | s1=123-45-6789 (SSN, from 3) | — |
| 10 | T1>A2 | PUBLIC  | s2=4471820 (MRN, from 4) | — |
| 11 | A1>U | PUBLIC  | — | — |
| 12 | A2>U | PUBLIC  | — | — |

**Timing (on).** total 1.3725 s: reset 0.1172, receiver 0.0613, interactions 0.7722, data_lineage 0.1504, classification 0.0321, label_lineage 0.0202, leg_ready+risk 0.2017, read 0.0174
OPA calls: 16

**Notes.** Picture 2 of #348. A1 and A2 call T1; A2's request arrives before T1 serves A1. T1(A1)>V1 belongs to A1's call, so it carries s1 (3>5) and nothing of A2's; before #348 the fold routed it from the latest inbound (A2's) and V1 saw no label: verdict allow.

---

# P3 — two callers, one value

cell `trunk/control/two-callers-one-value`  ·  status **GREEN**  ·  classifier **authored**: the model's findings subtracted at legs [5, 7, 9, 10]

```
U>A1[s1 -], U>A2[s1 -], A1>T1[s1 -], A2>T1[s1 -], T1(A1)>V1[s1@subject -]⚑, V1>T1[-], T1(A2)>V2[s1@subject -], V2>T1[-], T1>A1[s1@subject -], T1>A2[s1@subject -], A1>U[-], A2>U[-]
```

```
expected
              1      2      3      4      5      6      7      8      9      10     11     12     
              U>A1   U>A2   A1>T1  A2>T1  T1>V1⚑ V1>T1  T1>V2  V2>T1  T1>A1  T1>A2  A1>U   A2>U   
SSN     s1    ●      ┈┈┈┈┈┈●━━━━━━●━━━━━━●━━━━━━○·      ━━━━━━○·      ━━━━━━○━━━━━━○·      ·      
MRN     s2    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      

observed
              1      2      3      4      5      6      7      8      9      10     11     12     
              U>A1   U>A2   A1>T1  A2>T1  T1>V1⚑ V1>T1  T1>V2  V2>T1  T1>A1  T1>A2  A1>U   A2>U   
SSN     s1    ●      ┈┈┈┈┈┈●━━━━━━●━━━━━━●━━━━━━○·      ━━━━━━○·      ━━━━━━○━━━━━━○·      ·      
MRN     s2    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DOB     s3    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
DIS     s4    ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      ·      
neighbourhood at 3: s1 0 of 0
neighbourhood at 4: s1 0 of 0
neighbourhood at 5: s1 0 of 0
neighbourhood at 7: s1 0 of 0
neighbourhood at 9: s1 0 of 0
neighbourhood at 10: s1 0 of 0
verdict at 5: expected on=block / off=allow, observed on=block / off=allow
```

**Rows (inherited labels on).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A1 | RESTRICTED SSN=123-45-6789 | — | — |
| 2 | U>A2 | RESTRICTED SSN=123-45-6789 | — | — |
| 3 | A1>T1 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 1) | — |
| 4 | A2>T1 | RESTRICTED SSN=123-45-6789 | s1=123-45-6789 (SSN, from 2) | — |
| 5 | T1>V1⚑ | PUBLIC  | s1=123-45-6789 (SSN, from 3) | critical block ['T-SENS'] v1 |
| 6 | V1>T1 | PUBLIC  | — | — |
| 7 | T1>V2 | PUBLIC  | s1=123-45-6789 (SSN, from 4) | — |
| 8 | V2>T1 | PUBLIC  | — | — |
| 9 | T1>A1 | PUBLIC  | s1=123-45-6789 (SSN, from 3) | — |
| 10 | T1>A2 | PUBLIC  | s1=123-45-6789 (SSN, from 4) | — |
| 11 | A1>U | PUBLIC  | — | — |
| 12 | A2>U | PUBLIC  | — | — |

**Timing (on).** total 1.5384 s: reset 0.0924, receiver 0.0914, interactions 0.8737, data_lineage 0.1589, classification 0.0336, label_lineage 0.0276, leg_ready+risk 0.2438, read 0.017
OPA calls: 16

**Notes.** Picture 2 of #348 with the same value in both calls, so a wrong parent shows as an edge that should not exist: T1(A1)>V1 must inherit from A1's request (3>5), not from A2's, which arrived later (4>5). Before #348 the fold drew 4>5, and 3>5 and 4>7 were missing.

