# nmb_demo fixture

SYNTHETIC stub inputs for stream C (S8-S10) so it can be built and tested before streams A/B land their artifacts.

- `candidates.json`: four candidates named after the Bexsero antigens plus a decoy. Peptides are random amino-acid strings generated with seed 20261114; scores, coverage and structure confidence are invented. None of it is a prediction.
- `pathogen.json`: *N. meningitidis* (taxid 487). `susceptible_small_animal_model` is null on purpose, so S10 states that a challenge model must be checked rather than assuming one. `correlate_of_protection: hSBA` is the README's own statement for meningococcal B.
