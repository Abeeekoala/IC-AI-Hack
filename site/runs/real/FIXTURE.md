# nmb_fixture

A 40-protein projection of a real `vaxforge run --pathogen "Neisseria meningitidis"` (S0-S2), for building and testing
downstream stages without downloads, BLAST or PSORTb.  Written with the normal `RunStore` API, so
`vaxforge verify fixtures/nmb_fixture` passes.

| Part | Real? |
|---|---|
| taxonomy (S0), proteome sequences and UniProt metadata (S1) | real |
| virulence annotation (S2: identity / coverage / E-value vs VFDB set A) | real BLAST+ output |
| localisation labels | **stand-in** from UniProt subcellular-location text (`status: fixture_stand_in`), NOT PSORTb |
| Protegen data | omitted (licence not stated) |

Proteins: fHbp (Q9JXV4), NadA (Q9JXK7), NHBA (Q7DD37), PorA (P0DH58), the 8 strongest VFDB hits, and a seeded random sample.
Note NHBA has no VFDB hit and NadA only a moderate one - useful edge cases for ranking and for stream D's Bexsero test.
