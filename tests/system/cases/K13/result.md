# K13 — a persistent agent, the value arrives after it left

cell `across/rule/interval`  ·  status **GREEN**  ·  classifier **authored**: the model's findings subtracted at legs [3, 4, 5]

```
U>A[-], A>L[-], L>A[s1@subject -], A>T2[s1@subject -], T2>V2[s1@subject -]⚑, V2>T2[-], T2>A[-], A>U[-] ~1min> U>A[s1 -], A>U[-]
```

```
expected
conversation 1
              1           2           3           4           5           6           7           8           
              U>A         A>L         L>A         A>T2        T2>V2⚑      V2>T2       T2>A        A>U         
SSN     s1    ·           ·           ○           ┈┈┈┈┈┈┈┈┈┈┈○┈┈┈┈┈┈┈┈┈┈┈○·           ·           ·           
MRN     s2    ·           ·           ·           ·           ·           ·           ·           ·           
DOB     s3    ·           ·           ·           ·           ·           ·           ·           ·           
DIS     s4    ·           ·           ·           ·           ·           ·           ·           ·           

conversation 2
              9           10          
              U>A         A>U         
SSN     s1    ┈┈┈┈┈┈┈┈┈┈┈●·           
MRN     s2    ·           ·           
DOB     s3    ·           ·           
DIS     s4    ·           ·           

observed
conversation 1
              1           2           3           4           5           6           7           8           
              U>A         A>L         L>A         A>T2        T2>V2⚑      V2>T2       T2>A        A>U         
SSN     s1    ·           ·           ○           ┈┈┈┈┈┈┈┈┈┈┈○┈┈┈┈┈┈┈┈┈┈┈○·           ·           ·           
MRN     s2    ·           ·           ·           ·           ·           ·           ·           ·           
DOB     s3    ·           ·           ·           ·           ·           ·           ·           ·           
DIS     s4    ·           ·           ·           ·           ·           ·           ·           ·           

conversation 2
              9           10          
              U>A         A>U         
SSN     s1    ┈┈┈┈┈┈┈┈┈┈┈●·           
MRN     s2    ·           ·           
DOB     s3    ·           ·           
DIS     s4    ·           ·           
verdict at 5: expected off=allow / conv=allow / cross=allow, observed off=allow / conv=allow / cross=allow
```

**Rows (cross run).**

| leg | who | classification | inherited labels (from leg) | risk |
|---|---|---|---|---|
| 1 | U>A | PUBLIC  | — | — |
| 2 | A>L | PUBLIC  | — | — |
| 3 | L>A | PUBLIC  | — | — |
| 4 | A>T2 | PUBLIC  | — | — |
| 5 | T2>V2⚑ | PUBLIC  | — | none allow [] v1 |
| 6 | V2>T2 | PUBLIC  | — | — |
| 7 | T2>A | PUBLIC  | — | — |
| 8 | A>U | PUBLIC  | — | — |
| 9 | U>A | RESTRICTED SSN=123-45-6789 | — | — |
| 10 | A>U | PUBLIC  | — | — |

Risk observer calls: 10

**Notes.** Agent A is declared persistent and serves two users. In user B's conversation (legs 1-8) the LLM writes a number equal to user A's SSN, unlabelled, and A sends it on at 09:00:06. User A gives A that SSN at 09:01:16 (leg 9). A's legs arrive first, so the occurrence exists when B's leg is matched, but it is valid only after 09:01:16 (R1): no crossing, allow. Green on this branch only because nothing crosses at all here; the case means something once the crossing core exists, and its deliberate break (the valid interval ignored) must turn it red.

