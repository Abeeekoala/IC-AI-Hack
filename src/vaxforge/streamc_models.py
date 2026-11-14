"""Shared input models for stream C (S8-S10).

Stream A's `runs/<run_id>/` contract (docs/ARTIFACT_CONTRACT.md) and stream B's S3-S7 contract
(docs/CONTRACT_S3_S7.md) are both real. These models are the *view* stream C needs of the upstream
stages - the union of the fields S8, S9 and S10 read. `streamc_adapter.py` fills them from the S0, S2
and S7 artifacts (or from the S2 stand-in when no S3-S7 artifact exists); nothing else in stream C
reads an artifact.

Every field beyond identity is optional so stage outputs degrade gracefully
(e.g. the pipeline runs without a GPU, so S7 structure fields may be absent).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field

# `Candidate.rank_source` values. None means "not stated" and is read as an upstream S3 rank.
RANK_SOURCE_USER_SELECTION = "user_selection_order"  # the order a person listed S2 proteins in: NOT a prediction
RANK_SOURCE_S3 = "s3_rank"  # a rank taken straight from an S3 artifact (the artifact route does not produce it: B's S7 owns the final order)
# Position in stream B's S7 `final_candidates` (1 = best). S10 treats it as an S3-INFORMED rank, not as a person's order, but it is not
# the S3 rank: S7's final order percentile-ranks S3, T-cell, B-cell, coverage and structure terms with unvalidated weights.
RANK_SOURCE_S7_FINAL = "s7_final_order"


class Epitope(BaseModel):
    """One predicted epitope from S5."""

    peptide: str
    kind: Literal["T_class_I", "T_class_II", "B_linear", "B_conformational"]
    start: int | None = None
    end: int | None = None
    score: float | None = None
    hla: list[str] = Field(default_factory=list)  # restricting alleles for T-cell epitopes


class PathogenContext(BaseModel):
    """What the animal-study stage needs to know about the pathogen (S0/S1 + curator input)."""

    name: str
    taxid: int | None = None
    # None means "unknown - must be checked", which S10 surfaces rather than guessing.
    susceptible_small_animal_model: bool | None = None
    correlate_of_protection: str | None = None  # e.g. "hSBA" for meningococcal B; None = none agreed/known
    containment_level_note: str | None = None


class Candidate(BaseModel):
    """One ranked candidate antigen as seen by stream C."""

    candidate_id: str
    protein_name: str
    rank: int
    # Where `rank` comes from. None = not stated (S10 reads it as an S3 rank, as before).
    # RANK_SOURCE_USER_SELECTION = a person's list order: S10 then makes no S3 claim from it.
    # RANK_SOURCE_S7_FINAL = position in B's S7 final order: S3-informed, but not the S3 rank.
    rank_source: str | None = None
    length_aa: int | None = None
    sequence: str | None = None
    localisation: str | None = None  # S2; "unknown" is a real value
    protegenicity: float | None = None  # S3. Artifact route: Vaxign-ML percentile / 100, in [0, 1]; None when S3 ran its rule-based fallback
    epitopes: list[Epitope] = Field(default_factory=list)  # S5
    pc90: float | None = None  # S6. Artifact route: IEDB PC90 (an interpolated NUMBER OF epitope/HLA hits), not a fraction or a percent
    coverage_by_population: dict[str, float] = Field(default_factory=dict)  # S6. Artifact route: fraction of the population in [0, 1]
    structure_confidence: float | None = None  # S7 (top-k only). Artifact route: mean pLDDT of the Boltz-2 model, 0-100
    structure_validators: list[str] = Field(default_factory=list)  # S7: which refolder(s) actually ran
    evidence_refs: list[str] = Field(default_factory=list)  # upstream artifact digests, e.g. "s7:sha256:..."
    # Unit / basis of the values above, keyed by field name, ONLY where the source states it (the artifact route does; the file
    # route does not, and nothing is guessed). S10 prints it next to the value. Empty = unit not stated.
    units: dict[str, str] = Field(default_factory=dict)

    def epitopes_of(self, *kinds: str) -> list[Epitope]:
        return [e for e in self.epitopes if e.kind in kinds]


# ---------------------------------------------------------------- S8 additions: reagent quantities, cost estimate

QUANTITY_SOURCE = "S9 placeholder plan"


class StepQuantity(BaseModel):
    """One reagent volume that the S9 plan loads for an S8 step (volume loaded = volume drawn + dead volume).

    A PLACEHOLDER: it follows from `s9_sessions.RobotParams`, whose every number is a coarse default, not from a kit insert or an SOP.
    """

    name: str
    microlitres: int | float  # a whole number stays a whole number (the plan loads whole microlitres)
    source: str = QUANTITY_SOURCE


class CostItem(BaseModel):
    """One line of a cost estimate: a quantity, and a price only where the user supplied one (never a default)."""

    item: str  # key into `streamc.prices`: gene_synthesis, robot_session, tip:<rack>, labware:<load name>, reagent:<name>, animal
    quantity: int | float
    unit: str
    unit_price: float | None = None  # per `unit`
    currency: str | None = None
    subtotal: float | None = None  # quantity x unit_price; None while unpriced


class CostTotal(BaseModel):
    currency: str
    amount: float


class CostEstimate(BaseModel):
    """Quantities x user-supplied prices. `estimated` only when every item is priced; nothing is ever priced from a built-in table."""

    status: Literal["not_estimated", "partial", "estimated"]
    basis: str
    items: list[CostItem] = Field(default_factory=list)
    total: CostTotal | None = None  # only when status == "estimated"
    priced_subtotal: CostTotal | None = None  # the sum of the priced items alone (None when none is priced); NOT a total while items are unpriced
    unused_price_keys: list[str] | None = None  # stage-level estimate only: keys of the price table that match no item (usually a typo); None = not computed
    note: str = ""


# ---------------------------------------------------------------- directory names

_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_MAX_DIR_NAME = 64


def _base_dir_name(text: str) -> str:
    """Lower-case, filesystem-safe slug. Never '', '.', '..' or a Windows device name."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(text)).strip("_.").lower()[:_MAX_DIR_NAME].strip("_.")
    if not name:
        return "candidate"
    head, dot, tail = name.partition(".")  # Windows reserves `nul` and `nul.txt` alike: it is the part before the first dot that counts
    return f"{head}_{dot}{tail}" if head in _WINDOWS_RESERVED else name


def candidate_dir_names(ids: Sequence[str]) -> list[str]:
    """One directory name per candidate id, in the order given: filesystem-safe, unique and deterministic.

    The slug lower-cases and replaces other characters, so different ids can share a slug (`NadA` and `nada`,
    `P1/2` and `P1_2`). The first keeps the plain slug; later ones get `-2`, `-3`, ... in the order given
    (rank order), so a second candidate can never overwrite the first.
    """
    used: set[str] = set()
    out: list[str] = []
    for cid in ids:
        base = _base_dir_name(cid)
        name, n = base, 1
        while name in used:
            n += 1
            name = f"{base}-{n}"
        used.add(name)
        out.append(name)
    return out
