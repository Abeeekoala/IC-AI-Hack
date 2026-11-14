"""Adapter between upstream artifacts and stream C's input models.

Stream C reads upstream data in ONE place, here. There are two routes.

1. ARTIFACT ROUTE (the real one): `load_inputs_from_store(store, config)` reads the run's
   artifacts through `RunStore` (docs/ARTIFACT_CONTRACT.md).
     * pathogen    <- S0 `payload.taxon` (`scientific_name`, `taxid`). The animal-model and
                      correlate-of-protection fields stay None = unknown; they are never inferred.
     * candidates  <- stream B's S7 artifact through `_candidates_from_upstream` (docs/CONTRACT_S3_S7.md
                      sections 7 and 8; the mapping table is in that function's docstring and in
                      docs/streamc.md). `payload.final_candidates` is the order, `payload.candidates[]`
                      holds the fields, S2 supplies `localisation`. If NO S3-S7 artifact exists: the S2
                      STAND-IN. A person NAMES the proteins (`proteins=[...]` or config `streamc.proteins`:
                      gene, entry name, accession or protein_id, any case) and each becomes a `Candidate`
                      from S2's `payload.proteins[]` plus its sequence from the S1/S2 proteome FASTA. The
                      rank is the order given, `rank_source` says so, and every field that only S3-S7 could
                      fill stays empty (S10 then reports "not_testable_no_upstream_prediction"). If S3-S7
                      artifacts exist but cannot be used (S7 missing, not `ok`, empty, written in B's
                      standalone envelope) the stand-in is NOT used: `NoCandidatesError` carries B's reason.
                      With nothing to work on a stage writes `ok` + `payload.status: "not_run"` (see
                      `write_not_run`); nothing is picked silently.

2. FILE ROUTE (stand-alone and tests): `load_inputs(run_dir)` reads plain JSON, first hit wins, or passed
   explicitly:

    candidates:  final_candidates.json | candidates.json | ranked_candidates.json | s7_candidates.json
                 a list of candidate objects, or {"candidates": [...]}
    pathogen:    pathogen.json | taxonomy.json | s0_taxonomy.json
                 {"name"/"organism", "taxid", optional "susceptible_small_animal_model",
                  "correlate_of_protection", "containment_level_note"}

Per candidate only id/name/rank matter; everything else is optional and is passed through
untouched (S10 uses it to build the falsification map; missing fields become
"not_testable_no_upstream_prediction" there, never a guess). The file route accepts several spellings
of each field because it predates the contract; the artifact route does not need them.

Nothing here validates SCIENCE (units of pc90, direction of epitope scores, ...). Those are
listed in `ASSUMPTIONS` and echoed in stream C's summary so the integrator can check them.
"""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from vaxforge.artifacts import (
    REQUIRED_PAYLOAD_KEYS,
    STAGE_ORDER,
    Artifact,
    ContractError,
    RunStore,
    register_payload_keys,
)
from vaxforge.config import ConfigError
from vaxforge.fasta import read_fasta
from vaxforge.streamc_models import RANK_SOURCE_S7_FINAL, RANK_SOURCE_USER_SELECTION, Candidate, Epitope, PathogenContext

CANDIDATE_FILES = ("final_candidates.json", "candidates.json", "ranked_candidates.json", "s7_candidates.json")
PATHOGEN_FILES = ("pathogen.json", "taxonomy.json", "s0_taxonomy.json")
_PATHOGEN_NAME_KEYS = ("name", "pathogen", "organism", "scientific_name")

# Written on every artifact built from the S2 stand-in (exact text: the integrator greps for it).
STAND_IN_WARNING = (
    "S3-S7 not available: candidates are an explicit user selection from S2, rank is the order given, not a prediction"
)
DEFAULT_TOP_K = 3
B_STAGES = ("s3", "s4", "s5", "s6", "s7")  # stream B's stages; any of them present means the S2 stand-in is NOT used

# `payload.status` of a stage that had nothing to work on / whose outcome needs a person to look (stream D's report reads these words).
STATUS_NOT_RUN = "not_run"
STATUS_REFUSED = "refused"
STAGE_LABELS = {"s8": "S8", "s9": "S9", "s9plus": "S9+", "s10": "S10"}

ASSUMPTIONS = [
    "Candidate.pc90 and coverage_by_population are used as supplied on the file route (their unit is not checked). From stream B's S7 "
    "(artifact route): coverage_by_population is a FRACTION in [0, 1]; pc90 is IEDB's PC90 (an interpolated number of epitope/HLA hits that "
    "90% of the population is predicted to have), not a fraction or a percent. Candidate.units says so next to the value.",
    "Epitope.score is passed through but never used to rank or filter. From S7: B_linear carries BepiPred-3.0's region mean score (higher = more "
    "likely antibody contact); T-cell epitopes carry None because S5 reports a %Rank (lower = better) that one score field cannot hold beside it; "
    "conformational segments carry None.",
    "Epitope.hla should hold allele names such as HLA-A*02:01 (stream B writes this 2-field form, class II pairs as HLA-DQA1*05:01/DQB1*02:01); "
    "S10 uses them to name the transgenic lines a test needs.",
    "Candidate.rank is 1 = best. If absent, file order is used. From S7 it is the position in final_candidates (rank_source s7_final_order): "
    "an S3-informed order with unvalidated weights, not the S3 rank.",
    "Candidate.structure_confidence from S7 is the mean pLDDT (0-100) of the Boltz-2 model: confidence in the MODEL, not immunogenicity.",
    "Whether a licensed vaccine, a small-animal challenge model or a correlate of protection exists is NOT inferred; unknown stays unknown.",
]

_KIND_ALIASES = {
    "t_class_i": "T_class_I", "mhc-i": "T_class_I", "mhc_i": "T_class_I", "class i": "T_class_I", "class_i": "T_class_I", "mhci": "T_class_I", "cd8": "T_class_I",
    "t_class_ii": "T_class_II", "mhc-ii": "T_class_II", "mhc_ii": "T_class_II", "class ii": "T_class_II", "class_ii": "T_class_II", "mhcii": "T_class_II", "cd4": "T_class_II",
    "b_linear": "B_linear", "b-cell linear": "B_linear", "linear": "B_linear", "bepipred": "B_linear", "b_cell": "B_linear",
    "b_conformational": "B_conformational", "conformational": "B_conformational", "discotope": "B_conformational",
}


class RunInputs(BaseModel):
    candidates: list[Candidate]
    pathogen: PathogenContext
    warnings: list[str] = Field(default_factory=list)
    sources: dict[str, str] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=lambda: list(ASSUMPTIONS))
    # Artifact route only (empty on the file route):
    stages: list[str] = Field(default_factory=list)  # artifact stage ids actually read = the `inputs=` of a stage artifact
    rank_source: str | None = None  # where every candidate's `rank` comes from
    selection: dict[str, Any] = Field(default_factory=dict)  # what was asked for and what it matched (JSON-compatible)


def _first(d: dict[str, Any], *names: str, default: Any = None) -> Any:
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


def _to_float(x: Any) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def normalise_epitope(raw: dict[str, Any], warnings: list[str] | None = None) -> Epitope | None:
    peptide = _first(raw, "peptide", "sequence", "seq", "epitope")
    kind_raw = str(_first(raw, "kind", "type", "class", "category", default="")).strip().lower()
    kind = _KIND_ALIASES.get(kind_raw)
    if not peptide or kind is None:
        if warnings is not None:
            warnings.append(f"dropped an epitope with peptide={peptide!r} kind={kind_raw!r} (unrecognised)")
        return None
    hla = _first(raw, "hla", "allele", "alleles", default=[])
    if isinstance(hla, str):
        hla = [h.strip() for h in hla.replace(";", ",").split(",") if h.strip()]
    return Epitope(
        peptide=str(peptide), kind=kind, start=_first(raw, "start", "begin"), end=_first(raw, "end", "stop"),
        score=_to_float(_first(raw, "score", "affinity", "rank_score")), hla=list(hla),
    )


def normalise_candidate(raw: dict[str, Any], position: int, warnings: list[str] | None = None) -> Candidate:
    seq = _first(raw, "sequence", "seq")
    cid = str(_first(raw, "candidate_id", "id", "uniprot", "accession", "protein_id", default=f"cand{position}"))
    name = str(_first(raw, "protein_name", "name", "gene", "product", "description", default=cid))
    eps = []
    for e in _first(raw, "epitopes", default=[]) or []:
        ne = normalise_epitope(e, warnings)
        if ne is not None:
            eps.append(ne)
    cov = _first(raw, "coverage_by_population", "population_coverage", default={}) or {}
    return Candidate(
        candidate_id=cid, protein_name=name, rank=int(_first(raw, "rank", "position", default=position)),
        length_aa=_first(raw, "length_aa", "length", default=len(seq) if seq else None), sequence=seq,
        localisation=_first(raw, "localisation", "localization", "subcellular_location", "psortb"),
        protegenicity=_to_float(_first(raw, "protegenicity", "vaxign_ml", "score")),
        epitopes=eps, pc90=_to_float(_first(raw, "pc90", "pop_coverage_pc90")),
        coverage_by_population={str(k): float(v) for k, v in dict(cov).items()},
        structure_confidence=_to_float(_first(raw, "structure_confidence", "confidence")),
        structure_validators=list(_first(raw, "structure_validators", "validators", default=[]) or []),
        evidence_refs=[str(x) for x in (_first(raw, "evidence_refs", "evidence", default=[]) or [])],
    )


def normalise_pathogen(raw: dict[str, Any]) -> PathogenContext:
    tax = _first(raw, "taxid", "tax_id", "taxon_id")
    return PathogenContext(
        name=str(_first(raw, *_PATHOGEN_NAME_KEYS, default="unknown pathogen")),
        taxid=int(tax) if tax is not None else None,
        susceptible_small_animal_model=_first(raw, "susceptible_small_animal_model", "small_animal_model"),
        correlate_of_protection=_first(raw, "correlate_of_protection", "correlate"),
        containment_level_note=_first(raw, "containment_level_note", "containment"),
    )


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError; say which file
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc


def _find(run_dir: Path, names: tuple[str, ...]) -> Path | None:
    for n in names:
        if (run_dir / n).exists():
            return run_dir / n
    return None


def load_inputs(
    run_dir: str | Path | None = None,
    *,
    candidates_path: str | Path | None = None,
    pathogen_path: str | Path | None = None,
) -> RunInputs:
    """Read candidates + pathogen. Raises FileNotFoundError / ValueError with a message saying what was looked for."""
    warnings: list[str] = []
    sources: dict[str, str] = {}
    run = Path(run_dir) if run_dir else None
    cpath = Path(candidates_path) if candidates_path else (_find(run, CANDIDATE_FILES) if run else None)
    if cpath is None or not cpath.exists():
        raise FileNotFoundError(f"no candidates file: looked for {', '.join(CANDIDATE_FILES)} in {run_dir!s} (or pass --candidates)")
    raw = _load_json(cpath)
    items = raw.get("candidates") if isinstance(raw, dict) else raw
    if not isinstance(items, list) or not items:
        raise ValueError(f"{cpath} has no candidates (expected a non-empty list or {{'candidates': [...]}})")
    cands = []
    for i, c in enumerate(items):
        if not isinstance(c, dict):
            raise ValueError(f"{cpath}: candidate #{i + 1} is not a JSON object")
        try:
            cands.append(normalise_candidate(c, i + 1, warnings))
        except (TypeError, ValueError) as exc:  # e.g. rank "x", coverage_by_population that is not a mapping
            raise ValueError(f"{cpath}: candidate #{i + 1} is malformed: {exc}") from exc
    ids = [c.candidate_id for c in cands]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate candidate ids in {cpath}: {sorted({i for i in ids if ids.count(i) > 1})}")
    sources["candidates"] = str(cpath)

    ppath = Path(pathogen_path) if pathogen_path else (_find(run, PATHOGEN_FILES) if run else None)
    if ppath is not None and ppath.exists():
        praw = _load_json(ppath)
        if not isinstance(praw, dict):
            raise ValueError(f"{ppath} is not a JSON object")
        try:
            pathogen = normalise_pathogen(praw)
        except (TypeError, ValueError) as exc:  # e.g. taxid "x", susceptible_small_animal_model "maybe"
            raise ValueError(f"{ppath}: pathogen is malformed: {exc}") from exc
        if _first(praw, *_PATHOGEN_NAME_KEYS) is None:  # e.g. a stage-artifact envelope, whose data sits under "payload"
            warnings.append(f"{ppath} has no pathogen name (looked for {', '.join(_PATHOGEN_NAME_KEYS)}); using 'unknown pathogen'")
        sources["pathogen"] = str(ppath)
    else:
        pathogen = PathogenContext(name="unknown pathogen")
        warnings.append("no pathogen file found; using 'unknown pathogen' (S10 will say the challenge model and correlate must be checked)")
    return RunInputs(candidates=sorted(cands, key=lambda c: c.rank), pathogen=pathogen, warnings=warnings, sources=sources)


# =========================================================================== artifact route


class NoCandidatesError(ValueError):
    """There is nothing for S8 / S10 to work on: a stage turns this into an artifact `ok` + `payload.status: "not_run"` (`write_no_candidates`).

    The message says what is missing and how to fix it. `reason` is a short code, `suggestions` S2 proteins worth naming (offered, never applied),
    `stages` the artifact stage ids that were read before the problem was found (they become the artifact's `inputs=`), `pathogen` the S0 taxon
    when it was already read.
    """

    def __init__(self, message: str, *, reason: str, suggestions: list[dict[str, Any]] | None = None, stages: list[str] | None = None):
        super().__init__(message)
        self.reason = reason
        self.suggestions = list(suggestions or [])
        self.stages = list(stages or [])
        self.pathogen: dict[str, Any] | None = None


class ProteinSelectionError(NoCandidatesError):
    """The S2 stand-in selection is missing, unknown, ambiguous or repeated (only used when no S3-S7 artifact exists).

    `reason` is one of no_selection | unknown_protein | ambiguous_protein | duplicate_protein.
    """


class UpstreamNotUsable(NoCandidatesError):
    """Stream B's S3-S7 artifacts exist but cannot be used, so the S2 stand-in is deliberately NOT used instead.

    `reason` is one of upstream_incomplete (S7 missing) | upstream_not_ok (S7 status is not ok) | upstream_empty (S7 lists no final candidate)
    | upstream_wrong_envelope (written in B's standalone envelope, not RunStore's).
    """


@dataclass
class UpstreamCandidates:
    """What `_candidates_from_upstream` returns when stream B's S7 can be read."""

    candidates: list[Candidate]  # best first; `rank` 1 = best
    stages: list[str]  # artifact stage ids read, e.g. ["s2", "s7"]: they become (with s0) the stage's `inputs=`
    warnings: list[str] = field(default_factory=list)
    selection: dict[str, Any] = field(default_factory=dict)  # what was read (JSON-compatible); the caller adds `used` and `top_k`


def _num(x: Any) -> float | None:
    """A finite float from a JSON number, else None (never a default)."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return None if x != x or x in (float("inf"), float("-inf")) else float(x)


def _read_b(store: RunStore, stage: str) -> Artifact:
    """Read one of B's artifacts through RunStore. B's standalone envelope (`artifact_id`, no `digest`) gets its own clear message."""
    try:
        return store.read_artifact(stage)
    except ContractError as exc:
        try:
            raw = json.loads(store.artifact_path(stage).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = None
        if isinstance(raw, dict) and "artifact_id" in raw and "digest" not in raw:
            raise UpstreamNotUsable(
                f"{stage}: {store.artifact_path(stage).name} is stream B's standalone envelope (it has artifact_id and no digest), written by a "
                "stage run on a folder without manifest.json. Re-run S3-S7 on this run folder (it has manifest.json) so they write through RunStore.",
                reason="upstream_wrong_envelope") from exc
        raise


_LOCAL_PATH = re.compile(r"(?<![A-Za-z])(?:[A-Za-z]:[\\/]|/(?:Users|home|tmp|var|opt|mnt|root|private)/)[^\s;,)'\"]*")


def _scrub(text: Any) -> str:
    """B's reason strings, one line, without a local path: what we write is hashed into our artifacts, and a path is not something that was computed."""
    return " ".join(_LOCAL_PATH.sub("<local path>", str(text)).split())


def _first_warning(art: Artifact) -> str:
    return _scrub((art.warnings or ["no details recorded"])[0])[:300]


def _b_state_warnings(pl: dict[str, Any]) -> list[str]:
    """What B's payload says did NOT run, in words. B writes artifact status `ok` even then (the truth is in the payload), so read it here."""
    out: list[str] = []
    up = pl.get("upstream") if isinstance(pl.get("upstream"), dict) else {}
    ranker = up.get("s3_ranker") if isinstance(up.get("s3_ranker"), dict) else {}
    if ranker.get("backend") == "subtractive_filter":
        out.append("S3 ran its rule-based fallback (Vaxign-ML did not run): there is no protegenicity score, so the S3 falsification row has no "
                   "antigenicity prediction to test")
    leak = up.get("s3_leakage_audit") if isinstance(up.get("s3_leakage_audit"), dict) else {}
    if leak.get("holdout_enforced") is False:
        out.append("S3 LEAKAGE RISK (stream B's banner): the pretrained Vaxign-ML model was trained on all Protegen entries, so recovering known "
                   "antigens is not validation")
    if up.get("s4_incomplete_screening"):
        gates = ", ".join(str(g) for g in (up.get("s4_unavailable_gates") or [])) or "unknown"
        out.append(f"S4 screening was INCOMPLETE: gate(s) {gates} did not run, so these candidates were carried forward unscreened for them")
    s5 = up.get("s5_status") if isinstance(up.get("s5_status"), dict) else {}
    missing = [f"{name} ({v.get('status')}: {_scrub(v.get('reason') or 'no reason recorded')})" for name, v in s5.items()
               if isinstance(v, dict) and v.get("status") != "ran"]
    if missing:
        out.append("S5 analyses that did not run: " + "; ".join(missing)[:600] + ". The epitopes they would have produced do not exist")
    s6 = up.get("s6_analysis") if isinstance(up.get("s6_analysis"), dict) else {}
    if s6 and s6.get("status") != "ran":
        out.append(f"S6 population coverage did not run ({_scrub(s6.get('reason') or 'no reason recorded')}): there is no coverage data")
    analysis = pl.get("analysis") if isinstance(pl.get("analysis"), dict) else {}
    if analysis.get("degraded_to_sequence_only"):
        why = (analysis.get("folding") or {}).get("reason") if isinstance(analysis.get("folding"), dict) else None
        out.append(f"S7 DEGRADED to sequence-only ranking ({_scrub(why or 'no structure model was made')}): every structure field is empty and the final order "
                   "rests on the S3, T-cell, B-cell and coverage terms that exist")
    return out


def _candidates_from_upstream(store: RunStore) -> UpstreamCandidates | None:
    """Stream B's S7 final ranking as stream C candidates; None ONLY when no S3-S7 artifact exists (then the S2 stand-in applies).

    Entry point: `s7_structure.json`. `payload.final_candidates` is the final order (rank 1 = first), `payload.candidates[]` (matched by
    `protein_id`) carries the fields, `s2_annotate.json` supplies `localisation` (matched by `protein_id`; "Unknown" is kept as a value). B's
    code, not its prose, is the source of truth where they differ (docs/CONTRACT_S3_S7.md, section 8 and the mismatches listed in docs/streamc.md).
    Statuses are respected: S3-S7 artifacts that exist but cannot be used raise `UpstreamNotUsable` (the stand-in is NOT used); an S7 whose
    `final_candidates` names a protein that S2 or `candidates[]` lacks raises `ContractError`. B writes status `ok` even when nothing ran, so the
    payload-level states (degraded, incomplete screening, unavailable analyses, structure status) are read too and reported as warnings.

    Mapping (B field -> Candidate field; units are B's and are NOT converted):
      final_candidates position (1-based)             -> rank; rank_source = "s7_final_order" (S3-informed, NOT the S3 rank)
      candidates[].upstream.s3.protegenicity          -> protegenicity, Vaxign-ML percentile / 100 in [0, 1]; None when S3 used its rule-based
                                                         fallback. NOT scores.terms_raw.s3: in B's code that is S3's `composite_score`, which equals
                                                         protegenicity only with the default weights and a Vaxign-ML run (section 8 says otherwise)
      epitope_set.t_cell[] grouped by protein_id      -> epitopes kind T_class_I / T_class_II (`class` "I" / "II"), start, end (1-based inclusive),
                                                         hla = alleles (HLA-A*02:01 form); score None (B gives a %Rank, lower = better)
      candidates[].bcell.linear.regions[]             -> epitopes kind B_linear, start, end, peptide, score = mean_score (BepiPred-3.0, higher = better)
      structure.conformational_epitopes.epitope_segments
                                                      -> epitopes kind B_conformational, ONLY when its status is "ran" and sequence_matches_input is
                                                         true: start/end = the segment, peptide = that stretch of the candidate sequence, score None
      candidates[].coverage.by_population             -> coverage_by_population, FRACTION of the population in [0, 1]
      candidates[].coverage.by_mode_at_score_population[mode_used].pc90
                                                      -> pc90, IEDB PC90: an interpolated NUMBER of epitope/HLA hits (not a fraction or percent)
      structure.mean_plddt                            -> structure_confidence, mean pLDDT 0-100 of the Boltz-2 model (the MODEL's confidence)
      structure.validators with status "ran"          -> structure_validators (names); both structure fields only when structure.status == "ran"
                                                         and S7 did not degrade to sequence-only
      s7 artifact digest                              -> evidence_refs ["s7:<digest>"]
      units                                           -> Candidate.units says what protegenicity / pc90 / coverage / structure_confidence are
    A candidate with no epitopes, coverage or structure is fine: S10 reports those rows as `not_testable_no_upstream_prediction`.
    """
    present = [s for s in B_STAGES if store.has(s)]
    if not present:
        return None
    if "s7" not in present:
        raise UpstreamNotUsable(
            f"S3-S7 are only partly present ({', '.join(present)}) and s7 (the final ranking) is missing. Run S7 (python -m vaxforge.s7_structure "
            "--run-dir <run folder>) or remove the partial S3-S7 artifacts to use the S2 stand-in. The stand-in is not used while any of them exists.",
            reason="upstream_incomplete")
    s7 = _read_b(store, "s7")
    if s7.status != "ok":
        raise UpstreamNotUsable(
            f"s7 artifact has status {s7.status!r}: {_first_warning(s7)}. Fix S3-S7 and re-run S7 (python -m vaxforge.s7_structure --run-dir <run "
            "folder>). The S2 stand-in is not used while S3-S7 artifacts exist.", reason="upstream_not_ok", stages=["s7"])
    pl = s7.payload
    final, cands = pl.get("final_candidates"), pl.get("candidates")
    if not isinstance(final, list) or not isinstance(cands, list):
        raise ContractError("s7 payload has no final_candidates / candidates lists (docs/CONTRACT_S3_S7.md section 7)")
    if not final:
        raise UpstreamNotUsable(
            f"s7 final_candidates is empty ({len(cands)} candidate(s) in its candidates list): nothing was ranked for S8-S11. If S4 rejected "
            "every candidate, see s4_filter.json payload.rejected (s4.rescue and s4.conservancy.on_fail: flag keep candidates). The S2 stand-in is not "
            "used while S3-S7 artifacts exist.", reason="upstream_empty", stages=["s7"])
    s2 = store.read_artifact("s2").require_ok()
    by_id = {c.get("protein_id"): c for c in cands if isinstance(c, dict)}
    s2_rows = {p.get("protein_id"): p for p in (s2.payload.get("proteins") or []) if isinstance(p, dict)}
    t_cell: dict[str, list[Epitope]] = {}
    warnings = _b_state_warnings(pl)
    for e in (pl.get("epitope_set") or {}).get("t_cell") or []:
        kind = {"I": "T_class_I", "II": "T_class_II"}.get(str(e.get("class")))
        if kind is None or not e.get("peptide"):
            warnings.append(f"dropped an S5 T-cell epitope with class={e.get('class')!r} peptide={e.get('peptide')!r} (not class I or II)")
            continue
        t_cell.setdefault(str(e.get("protein_id")), []).append(Epitope(
            peptide=str(e["peptide"]), kind=kind, start=e.get("start"), end=e.get("end"), score=None, hla=[str(a) for a in (e.get("alleles") or [])]))
    degraded = bool((pl.get("analysis") or {}).get("degraded_to_sequence_only"))
    ranker = (pl.get("upstream") or {}).get("s3_ranker") or {}
    out: list[Candidate] = []
    for rank, pid in enumerate(final, start=1):
        rec = by_id.get(pid)
        if rec is None:
            raise ContractError(f"s7 lists {pid!r} in final_candidates but not in its candidates list (docs/CONTRACT_S3_S7.md section 7): the artifact is inconsistent")
        row = s2_rows.get(pid)
        if row is None:
            raise ContractError(
                f"s7 lists {pid!r} in final_candidates but s2_annotate.json has no protein with that protein_id: S3-S7 were built from a different S2 "
                f"artifact (s7 digest {s7.digest[:19]}..., s2 digest {s2.digest[:19]}...). Re-run S3-S7 on this run's S2.")
        out.append(_from_s7_record(rec, row, rank, pid, s7.digest, t_cell.get(pid, []), degraded, ranker, warnings))
    if any(e.kind == "B_conformational" for c in out for e in c.epitopes):
        warnings.append("S7 conformational segments are mapped onto the candidate sequence assuming DiscoTope-3.0 residue numbers are 1-based sequence "
                        "positions (stream B documents 1-based positions and sequence_matches_input was true); each 'peptide' is a run of consecutive "
                        "predicted epitope residues, not a linear epitope")
    return UpstreamCandidates(
        candidates=out, stages=["s2", "s7"], warnings=warnings,
        selection={"source": "s7_final_ranking", "stages": ["s2", "s7"], "n_final_candidates": len(final), "s7_top_k": pl.get("top_k"),
                   "degraded_to_sequence_only": degraded, "rank_source": RANK_SOURCE_S7_FINAL})


def _from_s7_record(rec: dict[str, Any], row: dict[str, Any], rank: int, pid: str, s7_digest: str, t_cell: list[Epitope], degraded: bool,
                    ranker: dict[str, Any], warnings: list[str]) -> Candidate:
    """One `Candidate` from one S7 `candidates[]` record (see `_candidates_from_upstream` for the mapping)."""
    seq = rec.get("sequence") if isinstance(rec.get("sequence"), str) and rec.get("sequence") else None
    loc = row.get("localisation") if isinstance(row.get("localisation"), dict) else {}
    if loc.get("accessibility") not in (None, "accessible"):
        warnings.append(f"{pid}: S2 localisation is {loc.get('label')} (accessibility {loc.get('accessibility')}); it is in S7's final order, "
                        "so check that it can be expressed and presented before choosing it")
    units: dict[str, str] = {}
    s3 = (rec.get("upstream") or {}).get("s3") if isinstance(rec.get("upstream"), dict) else None
    prot = _num((s3 or {}).get("protegenicity"))
    if prot is not None:
        units["protegenicity"] = f"{ranker.get('score_semantics') or 'S3 protegenicity in [0, 1]; score semantics not recorded'} [S3 ranker: {ranker.get('backend')}]"
    epitopes = list(t_cell)
    lin = (rec.get("bcell") or {}).get("linear") if isinstance(rec.get("bcell"), dict) else None
    for r in (lin or {}).get("regions") or []:
        if isinstance(r, dict) and r.get("peptide"):
            epitopes.append(Epitope(peptide=str(r["peptide"]), kind="B_linear", start=r.get("start"), end=r.get("end"), score=_num(r.get("mean_score"))))
    cov = rec.get("coverage") if isinstance(rec.get("coverage"), dict) else {}
    by_pop = cov.get("by_population") if isinstance(cov.get("by_population"), dict) else {}
    coverage = {str(k): v for k, v in ((k, _num(v)) for k, v in by_pop.items()) if v is not None}
    if coverage:
        units["coverage_by_population"] = "fraction of the population in [0, 1] (stream B S6; HLA loci treated as independent)"
    pc90 = None
    brief = (cov.get("by_mode_at_score_population") or {}).get(cov.get("mode_used")) if isinstance(cov.get("by_mode_at_score_population"), dict) else None
    if isinstance(brief, dict):
        pc90 = _num(brief.get("pc90"))
    if pc90 is not None:
        units["pc90"] = (f"IEDB PC90 for {cov.get('score_population')}, {cov.get('mode_used')} mode: the interpolated number of epitope/HLA hits that at "
                         "least 90% of the population is predicted to have (not a fraction or a percent)")
    st = rec.get("structure") if isinstance(rec.get("structure"), dict) else {}
    confidence, validators = None, []
    if st.get("status") == "ran" and not degraded:
        confidence = _num(st.get("mean_plddt"))
        # sorted: RunStore writes JSON with sorted keys, so B's own (config) order of validators is not in the artifact to be kept
        validators = sorted(str(n) for n, v in (st.get("validators") or {}).items() if isinstance(v, dict) and v.get("status") == "ran")
        if confidence is not None:
            units["structure_confidence"] = "mean pLDDT of the Boltz-2 model, 0-100: confidence in the MODEL, not a measure of immunogenicity"
        conf = st.get("conformational_epitopes") if isinstance(st.get("conformational_epitopes"), dict) else {}
        if conf.get("status") == "ran":
            if st.get("sequence_matches_input") is not True or seq is None:
                warnings.append(f"{pid}: conformational epitopes were not carried: the structure's sequence does not match the input sequence "
                                "(or is unknown), so residue numbers cannot be mapped")
            for seg in conf.get("epitope_segments") or []:
                ok = isinstance(seg, (list, tuple)) and len(seg) == 2 and all(isinstance(i, int) and not isinstance(i, bool) for i in seg)
                if not ok or seq is None or st.get("sequence_matches_input") is not True:
                    continue
                a, b = seg
                if not 1 <= a <= b <= len(seq):
                    warnings.append(f"{pid}: dropped conformational segment {list(seg)}: outside the {len(seq)}-residue sequence")
                    continue
                epitopes.append(Epitope(peptide=seq[a - 1:b], kind="B_conformational", start=a, end=b, score=None))
    elif not degraded and st.get("status") not in (None, "ran", "not_selected"):
        warnings.append(f"{pid}: S7 structure status is {st.get('status')!r} ({_scrub(st.get('reason') or 'no reason recorded')}): no structure fields")
    return Candidate(
        candidate_id=pid, protein_name=readable_name({"description": rec.get("name") or row.get("description"), "gene": rec.get("gene") or row.get("gene"),
                                                    "entry_name": row.get("entry_name"), "protein_id": pid}),
        rank=rank, rank_source=RANK_SOURCE_S7_FINAL, length_aa=len(seq) if seq else rec.get("length"), sequence=seq,
        localisation=loc.get("label"), protegenicity=prot, epitopes=epitopes, pc90=pc90, coverage_by_population=coverage,
        structure_confidence=confidence, structure_validators=validators, evidence_refs=[f"s7:{s7_digest}"], units=units)


def setting(config: Any, key: str, default: Any = None) -> Any:
    """`config.get("streamc.<key>", default)`; works with config=None. Never requires a key to exist."""
    return config.get(f"streamc.{key}", default) if config is not None else default


def int_setting(config: Any, key: str, default: int, *, minimum: int = 1) -> int:
    raw = setting(config, key, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ConfigError(f"streamc.{key} must be an integer, got {raw!r}") from None
    if value < minimum:
        raise ConfigError(f"streamc.{key} must be at least {minimum}, got {value}")
    return value


def requested_proteins(config: Any = None, proteins: Any = None) -> list[str]:
    """The proteins a person named: the `proteins` argument, else config `streamc.proteins`. A string is split on commas."""
    raw = proteins if proteins is not None else setting(config, "proteins")
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, (list, tuple)):
        raise ConfigError(f"streamc.proteins must be a list of names (or one comma-separated string), got {raw!r}")
    return [str(x).strip() for x in raw if str(x).strip()]


_NAME_KEYS = ("protein_id", "entry_name", "accession", "gene")


def _name_index(proteins: list[dict[str, Any]]) -> dict[str, list[int]]:
    idx: dict[str, list[int]] = {}
    for i, p in enumerate(proteins):
        for k in _NAME_KEYS:
            v = p.get(k)
            if isinstance(v, str) and v.strip():
                hits = idx.setdefault(v.strip().lower(), [])
                if i not in hits:
                    hits.append(i)
    return idx


def _brief(p: dict[str, Any]) -> dict[str, Any]:
    v, loc = p.get("virulence") or {}, p.get("localisation") or {}
    return {
        "protein_id": p.get("protein_id"), "gene": p.get("gene"), "entry_name": p.get("entry_name"),
        "accession": p.get("accession"), "description": p.get("description"),
        "virulence_evidence": v.get("evidence"), "localisation": loc.get("label"), "accessibility": loc.get("accessibility"),
    }


def _brief_text(b: dict[str, Any]) -> str:
    bits = [str(x) for x in (b["entry_name"], b["accession"]) if x]
    return f"{b['gene'] or b['entry_name'] or b['protein_id']} ({', '.join(bits)}): {b['description'] or 'no description'}"


def suggest_proteins(proteins: list[dict[str, Any]], n: int = 5) -> list[dict[str, Any]]:
    """S2 proteins worth naming: strong/moderate virulence evidence AND accessible localisation, strongest first.

    If none is accessible (for example PSORTb did not run, so every label is Unknown) the strong/moderate ones are
    listed anyway with their accessibility shown. Suggestions are only offered, never applied.
    """
    order = {"strong": 0, "moderate": 1}
    pool = [(i, p) for i, p in enumerate(proteins) if ((p.get("virulence") or {}).get("evidence")) in order]
    accessible = [(i, p) for i, p in pool if ((p.get("localisation") or {}).get("accessibility")) == "accessible"]
    chosen = accessible or pool
    chosen.sort(key=lambda ip: (order[ip[1]["virulence"]["evidence"]], -float(((ip[1]["virulence"].get("best_hit") or {}).get("bitscore")) or 0), ip[0]))
    return [_brief(p) for _i, p in chosen[:n]]


def _close_matches(proteins: list[dict[str, Any]], idx: dict[str, list[int]], name: str, n: int = 5) -> list[dict[str, Any]]:
    key = name.strip().lower()
    found: list[int] = []

    def add(i: int) -> None:
        if i not in found:
            found.append(i)

    for k, hits in idx.items():
        if key and ((len(key) >= 2 and key in k) or (len(k) >= 3 and k in key)):
            for i in hits:
                add(i)
    for k in difflib.get_close_matches(key, list(idx), n=n, cutoff=0.6):
        for i in idx[k]:
            add(i)
    if len(key) >= 3:
        for i, p in enumerate(proteins):
            if key in str(p.get("description") or "").lower():
                add(i)
    return [_brief(proteins[i]) for i in found[:n]]


def match_proteins(proteins: list[dict[str, Any]], names: list[str]) -> list[int]:
    """Index into `proteins` for each name, in the order given. Raises ProteinSelectionError, never guesses."""
    idx = _name_index(proteins)
    out: list[int] = []
    for name in names:
        hits = idx.get(name.strip().lower(), [])
        if not hits:
            close = _close_matches(proteins, idx, name)
            hint = ("Close matches: " + "; ".join(_brief_text(b) for b in close)) if close else "No close match; see data/s2_annotations.tsv for the names."
            raise ProteinSelectionError(
                f"Unknown protein {name!r}: no S2 gene, entry name, accession or protein_id matches it (any case). {hint}",
                reason="unknown_protein", suggestions=close)
        if len(hits) > 1:
            both = [_brief(proteins[i]) for i in hits]
            raise ProteinSelectionError(
                f"Protein name {name!r} is ambiguous: it matches {len(hits)} proteins ({'; '.join(_brief_text(b) for b in both)}). "
                "Use the accession or protein_id instead.", reason="ambiguous_protein", suggestions=both)
        if hits[0] in out:
            raise ProteinSelectionError(
                f"{name!r} names a protein already selected ({proteins[hits[0]].get('protein_id')}); list each protein once.",
                reason="duplicate_protein", suggestions=[_brief(proteins[hits[0]])])
        out.append(hits[0])
    return out


def readable_name(p: dict[str, Any]) -> str:
    """A human-readable protein name from S2's description / gene / entry name (never the bare FASTA id unless nothing else exists)."""
    gene, desc = str(p.get("gene") or "").strip(), str(p.get("description") or "").strip()
    if desc and gene and gene.lower() not in desc.lower():
        return f"{desc} ({gene})"
    return desc or gene or str(p.get("entry_name") or p.get("protein_id") or "protein")


def _sequences(store: RunStore, s2: Artifact, wanted: set[str]) -> dict[str, str]:
    """Sequences for the wanted protein ids, read through the S2 payload's file reference (hash-checked)."""
    ref = (s2.payload.get("proteome") or {}).get("file")
    if not isinstance(ref, dict):
        raise ContractError("s2 payload has no proteome.file reference, so sequences cannot be read")
    problem = store.verify_ref(ref)
    if problem:
        raise ContractError(f"s2 proteome file: {problem}")
    seqs = {rec.id: rec.seq for rec in read_fasta(store.resolve(ref)) if rec.id in wanted}
    missing = sorted(wanted - set(seqs))
    if missing:
        raise ContractError(f"s2 lists protein(s) that {ref.get('path')} does not contain: {', '.join(missing)}")
    return seqs


def load_inputs_from_store(
    store: RunStore, config: Any = None, *, proteins: Any = None, top_k: int | None = None
) -> RunInputs:
    """Candidates and pathogen for stream C, read from the run's artifacts. See the module docstring.

    Raises ContractError (S0/S2 missing or not ok, broken file reference, an S7 that contradicts S2), ConfigError (bad settings) and
    NoCandidatesError (nothing to work on: nothing named and no S3-S7 artifact, a name that does not resolve, or S3-S7 artifacts that
    cannot be used) - the last is not a failure: the stages turn it into `ok` + `payload.status: "not_run"` with the reasons.
    """
    k = int(top_k) if top_k is not None else int_setting(config, "top_k", DEFAULT_TOP_K)
    if k < 1:
        raise ConfigError(f"top_k must be at least 1, got {k}")
    s0 = store.read_artifact("s0").require_ok()
    taxon = s0.payload.get("taxon") or {}
    if not taxon.get("scientific_name"):
        raise ContractError("s0 payload has no taxon.scientific_name")
    taxid = taxon.get("taxid")
    # susceptible_small_animal_model / correlate_of_protection stay None = unknown: no artifact states them.
    pathogen = PathogenContext(name=str(taxon["scientific_name"]), taxid=int(taxid) if taxid is not None else None)
    stages = ["s0"]
    sources = {"pathogen": "s0.payload.taxon"}
    pathogen_json = {"scientific_name": pathogen.name, "taxid": pathogen.taxid}

    try:
        up = _candidates_from_upstream(store)
    except NoCandidatesError as exc:
        exc.stages = sorted({*stages, *exc.stages}, key=_stage_pos)
        exc.pathogen = pathogen_json
        raise
    if up is not None:
        cands = sorted(up.candidates, key=lambda c: c.rank)[:k]
        warnings = list(up.warnings)
        if len(up.candidates) > k:
            warnings.append(f"S7 ranked {len(up.candidates)} candidates but top_k is {k}: only the first {k} are used "
                            f"(not used: {', '.join(c.candidate_id for c in sorted(up.candidates, key=lambda c: c.rank)[k:])})")
        named = requested_proteins(config, proteins)
        if named:
            warnings.append(f"streamc.proteins ({', '.join(named)}) is ignored: S3-S7 artifacts exist, so the candidates are S7's final order. "
                            "Remove the setting, or remove the S3-S7 artifacts to use the S2 stand-in.")
        sources["candidates"] = "s7.payload.final_candidates + s7.payload.candidates + s2.payload.proteins (localisation)"
        return RunInputs(candidates=cands, pathogen=pathogen, warnings=warnings, sources=sources,
                         stages=sorted({*stages, *up.stages}, key=_stage_pos), rank_source=RANK_SOURCE_S7_FINAL,
                         selection={**up.selection, "used": [c.candidate_id for c in cands], "top_k": k})

    # ---- the S2 stand-in: only when NO S3-S7 artifact exists ------------------------------
    s2 = store.read_artifact("s2").require_ok()
    stages.append("s2")
    rows: list[dict[str, Any]] = list(s2.payload.get("proteins") or [])
    names = requested_proteins(config, proteins)
    if not names:
        sugg = suggest_proteins(rows)
        lines = "; ".join(_brief_text(b) + f" [{b['virulence_evidence']}, {b['localisation']}/{b['accessibility']}]" for b in sugg)
        err = ProteinSelectionError(
            "No candidates selected. S3-S7 (ranking, filters, epitopes) are not available, so name the proteins to carry into "
            "S8-S10: set config streamc.proteins to a list of gene, entry name, accession or protein_id (or pass proteins=[...]), or run "
            "S3-S7 (python -m vaxforge.s3_rank --run-dir <run folder>, then s4_filter, s5_epitopes, s6_coverage, s7_structure). "
            + (f"Suggestions from S2 (strong/moderate virulence evidence, accessible where known): {lines}." if sugg else
               "S2 shows no protein with strong or moderate virulence evidence; pick names from data/s2_annotations.tsv."),
            reason="no_selection", suggestions=sugg, stages=stages)
        err.pathogen = pathogen_json
        raise err
    try:
        picked = match_proteins(rows, names)
    except ProteinSelectionError as exc:
        exc.stages = list(stages)
        exc.pathogen = pathogen_json
        raise
    use = picked[:k]
    seqs = _sequences(store, s2, {rows[i]["protein_id"] for i in use})
    warnings = [STAND_IN_WARNING]
    if len(picked) > k:
        warnings.append(f"{len(picked)} proteins were named but top_k is {k}: only the first {k} are used "
                        f"(not used: {', '.join(rows[i]['protein_id'] for i in picked[k:])})")
    cands: list[Candidate] = []
    for rank, i in enumerate(use, start=1):
        p = rows[i]
        pid, seq = str(p["protein_id"]), seqs[str(p["protein_id"])]
        loc = p.get("localisation") or {}
        if p.get("length") is not None and int(p["length"]) != len(seq):
            warnings.append(f"{pid}: S2 records length {p['length']} but the proteome sequence has {len(seq)} residues; using {len(seq)}")
        if loc.get("accessibility") != "accessible":
            warnings.append(f"{pid}: S2 localisation is {loc.get('label')} (accessibility {loc.get('accessibility')}); "
                            "the protein was chosen by the user and no S3-S7 filter has screened it")
        cands.append(Candidate(candidate_id=pid, protein_name=readable_name(p), rank=rank, rank_source=RANK_SOURCE_USER_SELECTION,
                               length_aa=len(seq), sequence=seq, localisation=loc.get("label")))
    sources["candidates"] = "s2.payload.proteins + " + str((s2.payload.get("proteome") or {}).get("file", {}).get("path"))
    return RunInputs(
        candidates=cands, pathogen=pathogen, warnings=warnings, sources=sources, stages=stages,
        rank_source=RANK_SOURCE_USER_SELECTION,
        selection={"source": "s2_stand_in", "requested": names, "matched": [rows[i]["protein_id"] for i in picked],
                   "used": [c.candidate_id for c in cands], "top_k": k})


def _stage_pos(stage: str) -> int:
    return STAGE_ORDER.index(stage) if stage in STAGE_ORDER else len(STAGE_ORDER)


@contextmanager
def payload_keys_applied(stage: str, keys: Iterable[str]) -> Iterator[None]:
    """Apply A's structural payload check (`register_payload_keys`) for `stage` only while the block runs, then restore it.

    `REQUIRED_PAYLOAD_KEYS` is process-global state. Registering at import time would make every process that merely imports a
    stream C module demand these keys from ANY writer of that stage (stream A's own tests write toy s8/s9 payloads).
    """
    before = REQUIRED_PAYLOAD_KEYS.get(stage)
    register_payload_keys(stage, keys)
    try:
        yield
    finally:
        if before is None:
            REQUIRED_PAYLOAD_KEYS.pop(stage, None)
        else:
            register_payload_keys(stage, before)


def write_not_run(
    store: RunStore,
    stage: str,
    keys: Iterable[str],
    payload: dict[str, Any],
    reasons: list[str],
    *,
    params: dict[str, Any],
    inputs: list[str],
    tools: dict[str, str] | None = None,
    extra_warnings: Iterable[str] = (),
) -> Artifact:
    """Write the artifact of a stage that had nothing to work on: status `ok`, `payload.status == "not_run"`, `payload.reasons`.

    Why `ok`: `vaxforge run` returns EXIT_ERROR for any stage artifact whose status is not `ok`, and an additive stage with nothing to do
    must not fail a plain S0-S2 run (stream D's S9.5 does the same). The truth is in the payload, where `vaxforge.report` reads it, and in the
    first warning: "<STAGE> not run: <reasons>". `payload` holds the stage's own keys (`keys`, A's structural check applies) with nothing computed
    in them; a value that was not computed is None or empty, never a made-up zero.
    """
    body = {"status": STATUS_NOT_RUN, "reasons": list(reasons), **payload}
    warnings = [f"{STAGE_LABELS.get(stage, stage)} not run: " + "; ".join(reasons), *extra_warnings]
    with payload_keys_applied(stage, keys):
        return store.write_artifact(stage, body, params=params, inputs=inputs, tools=tools or {}, status="ok", warnings=warnings)


def write_no_candidates(store: RunStore, stage: str, err: NoCandidatesError, keys: Iterable[str], empty: dict[str, Any], *, params: dict[str, Any]) -> Artifact:
    """`write_not_run` for S8 / S10 when `load_inputs_from_store` raised `NoCandidatesError` (nothing named, S3-S7 unusable, ...).

    `empty` holds the stage's own payload keys with nothing in them; the pathogen, the selection state and S2 suggestions are added here.
    """
    payload = {"pathogen": err.pathogen, "selection": {"source": "none", "reason": err.reason}, "suggestions": err.suggestions, **empty}
    return write_not_run(store, stage, keys, payload, [str(err)], params=params, inputs=err.stages)
