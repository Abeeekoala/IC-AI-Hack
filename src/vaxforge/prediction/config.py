"""Defaults and loader for the S3-S7 configuration.

Stream A owns the repo-level ``config.yaml``. The keys A already documents in the project
README (section 5) are honoured verbatim: ``thresholds``, ``epitopes``, ``structure`` and
``reproducibility``. Everything specific to S3-S7 lives under ``s3`` ... ``s7``,
``refdata`` and ``tools`` so that it can be pasted into ``config.yaml`` without clashes.

Load order (later wins): ``DEFAULTS`` < YAML file < ``overrides`` dict.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

from .errors import ConfigError

# The 16 IEDB geographic areas plus the pooled "World" population (IEDB Population
# Coverage help page: "115 countries and 21 different ethnicities grouped into 16
# different geographical areas").
IEDB_AREAS = [
    "Central Africa", "Central America", "East Africa", "East Asia", "Europe",
    "North Africa", "North America", "Northeast Asia", "Oceania", "South Africa",
    "South America", "South Asia", "Southeast Asia", "Southwest Asia", "West Africa",
    "West Indies",
]

DEFAULTS: dict[str, Any] = {
    "run": {"top_k": 20},
    # ---- keys already defined by the project README (section 5) -----------------
    "thresholds": {
        "protegenicity": [0.90, 0.85],   # always report both
        "allergenicity": 0.45,           # AlgPred 2.0
        "toxicity": 0.80,                # ToxinPred2
        "conservancy_identity": 0.80,
    },
    "epitopes": {
        "bcell_tool": "bepipred-3.0",    # never 2.0
        "mhc_class_ii_weight": 0.3,      # down-weighted deliberately
    },
    "structure": {
        "engine": "boltz-2",             # MIT; never AF3 weights
        "refolder": ["esmfold", "af2-no-msa"],
    },
    "reproducibility": {"seed": 20261114, "pin_versions": True},
    # ---- reference data (cache-first) ----------------------------------------------
    "refdata": {
        "cache_dir": "cache",
        "offline": False,                # True: never touch the network, fail loudly instead
        "human_proteome": None,          # path to FASTA; None -> cache-first UniProt Swiss-Prot human
        "human_reviewed_only": True,
        "conservancy_proteomes": [],     # explicit FASTA paths; [] -> look up other strains on UniProt
        "iedb_human_ligands": None,      # one peptide per line, or IEDB mhc_ligand_full.csv
        "afnd_frequencies": None,        # IEDB "user population" TSV (see docs/CONTRACT_S3_S7.md)
        "afnd_sample_sizes": None,       # optional TSV/JSON: population -> n subjects
    },
    # ---- external tools: None = auto-detect on PATH / default location --------------
    "tools": {
        "docker": "docker",
        "blast_mode": "local",           # local | docker
        "blast_docker_image": "ncbi/blast:latest",
        "blast_threads": 4,
        "algpred2": None,                # command string, e.g. "algpred2" or "python /opt/algpred2/algpred2.py"
        "toxinpred2": None,
        "netmhcpan": None,               # path to the netMHCpan wrapper script
        "netmhciipan": None,
        "mhcflurry": None,               # unused (python API); presence checked by import
        "bepipred3": None,               # command string, e.g. "python /opt/BepiPred-3.0/bepipred3_CLI.py"
        "discotope3": None,              # command string, e.g. "python /opt/DiscoTope-3.0/discotope3/main.py"
        "boltz": None,                   # defaults to "boltz" on PATH
        "colabfold_batch": None,
    },
    # ---- S3 --------------------------------------------------------------------------
    "s3": {
        "backend": "auto",               # auto | vaxign_ml | scores_file | subtractive_filter
        "organism_type": "auto",         # auto | gram- | gram+ | virus
        "vaxign_ml": {
            "docker_image": "e4ong1031/vaxign-ml:latest",
            "use_sudo": False,
            "processes": None,           # None -> let Vaxign-ML choose; must be < container CPU count
            "saved_model_dir": None,     # custom (e.g. LOPOV-retrained) model dir from Train.sh
            "timeout_s": 4 * 3600,
            "extra_docker_args": [],
        },
        "scores_file": None,             # TSV/JSON of precomputed protegenicity (see contract)
        # Composite = weighted mean of available terms in [0, 1]. Default = pure Vaxign-ML ordering
        # (the README's "baseline"); the validation harness may re-weight (README: V -> S3).
        "weights": {"protegenicity": 1.0, "localisation": 0.0, "virulence": 0.0},
        # Used only by the rule-based fallback, which has no protegenicity term.
        "fallback_weights": {"localisation": 0.6, "virulence": 0.4},
        # Project-chosen priors (NOT from a publication). 'Unknown' is kept explicit and neutral.
        "localisation_prior": {
            "Extracellular": 1.0, "OuterMembrane": 1.0, "CellWall": 0.9, "Periplasmic": 0.5,
            "CytoplasmicMembrane": 0.4, "Multiple": 0.5, "Unknown": 0.5, "Cytoplasmic": 0.0,
        },
        "virulence": {"min_identity": 0.30, "min_coverage": 0.50, "max_evalue": 1e-5},
        "holdout": {"taxids": [], "note": None},
        "min_candidates": 30,            # never starve S4: keep at least this many even if < threshold
        "max_candidates": 150,
        "sanitize": {"max_ambiguous_fraction": 0.05},
    },
    # ---- S4 --------------------------------------------------------------------------
    "s4": {
        "on_unavailable": "carry",       # carry (flag + banner) | fail
        "rescue": [],                    # protein_ids to keep despite a rejection (recorded as rescued)
        "human_homology": {
            "enabled": True, "evalue": 1e-3, "min_identity": 0.35, "min_coverage": 0.50,
            "shared_peptide_length": 9, "ligand_recheck": True,
        },
        "allergenicity": {"enabled": True, "model": 1},
        "toxicity": {"enabled": True, "model": 1},
        "conservancy": {
            "enabled": True, "evalue": 1e-5, "min_coverage": 0.80,
            "n_strains": 2, "min_fraction_strains": 1.0,
            # reject (README default) | flag. On real N. meningitidis data "reject" removes fHbp, NHBA and NadA (the Bexsero
            # antigens), which are strain-variable by design; use "flag" to keep them and decide downstream.
            "on_fail": "reject",
        },
    },
    # ---- S5 --------------------------------------------------------------------------
    "s5": {
        "class_i": {
            "backend": "auto",           # auto | netmhcpan | mhcflurry
            "panel": "reference27", "lengths": [8, 9, 10, 11],
            "strong_rank": 0.5, "weak_rank": 2.0, "alleles_per_call": 6,
        },
        "class_ii": {
            "enabled": True, "backend": "netmhciipan",
            "panel": "reference26", "length": 15,
            "strong_rank": 1.0, "weak_rank": 5.0, "alleles_per_call": 4,
        },
        "min_strong_alleles_promiscuous": 3,   # an epitope is "promiscuous" if >= this many panel alleles bind it STRONGLY
        "half_score_density": 5.0,             # promiscuous epitopes / 100 aa at which tcell score = 0.5 (score = d / (d + this))
        "bcell": {
            "pred": "vt_pred", "threshold": 0.1512, "chunk_size": 1000, "chunk_overlap": 100,
            "min_epitope_len": 5, "merge_gap": 1, "esm_dir": None,
        },
        "conformational": {"enabled": True, "struc_type": "alphafold", "score_threshold": 0.90},
        "select": {"max_class_i": 8, "max_class_ii": 6, "min_alleles": 2},
        "sanitize": {"max_ambiguous_fraction": 0.05},
    },
    # ---- S6 --------------------------------------------------------------------------
    "s6": {
        "modes": ["class_i", "class_ii", "combined"],
        "score_population": "World",
        "regions": ["World"] + IEDB_AREAS,
        "fairness": {"reference_population": "Europe", "budget_k": 10, "budgets": [1, 2, 3, 5, 10], "mode": "combined"},
    },
    # ---- S7 --------------------------------------------------------------------------
    "s7": {
        "top_k": None,                   # None -> run.top_k
        "boltz": {
            "accelerator": "auto", "msa": "server",    # accelerator: auto|gpu|cpu|tpu ; msa: server | empty
            "recycling_steps": 3, "sampling_steps": 200, "diffusion_samples": 1,
            "use_potentials": False, "timeout_s": 2 * 3600, "max_residues": 1500,
            "extra_args": [],
        },
        "esmfold": {
            "mode": "local",             # local (needs transformers+torch, no network) | api (opt-in: sends the sequence to Meta's ESM Atlas server)
            "api_url": "https://api.esmatlas.com/foldSequence/v1/pdb/", "max_residues_api": 400,
        },
        "conformational_on_boltz": True,
        # Unvalidated priors; intended to be re-tuned by the validation harness.
        "final_weights": {"s3": 0.40, "tcell": 0.20, "bcell": 0.10, "coverage": 0.20, "structure": 0.10},
        "plddt_floor_for_epitope_confidence": 70.0,
    },
}

_KNOWN_REFOLDERS = {"esmfold", "af2-no-msa"}


def deep_merge(base: dict, extra: Mapping) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, Mapping) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _map_stream_a(raw: Mapping, cache_dir: Any = None, offline: Any = None) -> dict:
    """Overrides for stream B from a Stream A style config dict (see ``coerce_config``)."""
    over: dict = {k: copy.deepcopy(raw[k]) for k in _OWN_SECTIONS if k in raw}
    cache_dir = cache_dir or (raw.get("paths") or {}).get("cache_dir")
    if cache_dir is not None:
        over.setdefault("refdata", {})["cache_dir"] = str(cache_dir)
    off = (raw.get("network") or {}).get("offline") if offline is None else offline
    if off is not None:
        over.setdefault("refdata", {})["offline"] = bool(off)
    top_k = (raw.get("pipeline") or {}).get("top_k")
    if top_k is not None:
        over.setdefault("run", {}).setdefault("top_k", top_k)
    tools = dict(over.get("tools") or {})
    img = tools.pop("blast_image", None)
    if img:
        tools.setdefault("blast_docker_image", img)
    tools.pop("blast_bin_dir", None)
    over["tools"] = {k: v for k, v in tools.items() if v is not None}
    return over


def load_config(path: str | Path | None = None, overrides: Mapping | None = None) -> dict:
    """Defaults < YAML file (stream B layout OR the team's config.yaml, whose shared sections are mapped) < overrides."""
    cfg = copy.deepcopy(DEFAULTS)
    if path:
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"config file not found: {p}")
        try:
            import yaml  # lazy: PyYAML is only needed when a YAML file is actually used
        except ImportError as e:  # pragma: no cover
            raise ConfigError("PyYAML is required to read config.yaml (pip install pyyaml)") from e
        data = yaml.safe_load(p.read_text()) or {}
        if not isinstance(data, dict):
            raise ConfigError(f"{p}: top level must be a mapping")
        cfg = deep_merge(cfg, _map_stream_a(data))
    if overrides:
        cfg = deep_merge(cfg, overrides)
    validate(cfg)
    return cfg


_OWN_SECTIONS = ("thresholds", "epitopes", "structure", "reproducibility", "run", "refdata", "tools", "s3", "s4", "s5", "s6", "s7")


def coerce_config(cfg: Any) -> dict:
    """Accept ``None``, a plain dict (stream-B shape), or Stream A's ``vaxforge.config.Config`` (``config.yaml``).

    Stream A's sections that overlap with ours are mapped: ``paths.cache_dir`` -> ``refdata.cache_dir``,
    ``network.offline`` -> ``refdata.offline``, ``pipeline.top_k`` -> ``run.top_k``, ``tools.blast_image`` ->
    ``tools.blast_docker_image``. The README section 5 keys (``thresholds``, ``epitopes``, ``structure``,
    ``reproducibility``) are shared verbatim. Everything else in their file is ignored here.
    """
    if cfg is None:
        return load_config()
    if isinstance(cfg, Mapping) and "refdata" in cfg and "s4" in cfg and "s7" in cfg:
        return dict(cfg)                                   # already a fully resolved stream-B config
    raw = getattr(cfg, "raw", cfg)
    raw = raw() if callable(raw) else raw
    if not isinstance(raw, Mapping):
        raise ConfigError(f"cannot interpret config of type {type(cfg).__name__}")
    cache_dir = getattr(cfg, "cache_dir", None)
    offline = getattr(cfg, "offline", None)
    over = _map_stream_a(raw, cache_dir=cache_dir, offline=None if callable(offline) else offline)
    return load_config(overrides=over)


def validate(cfg: Mapping) -> None:
    """Enforce the project rules that cause rework if violated (README sections 2, 5, 9)."""
    bt = str(cfg["epitopes"]["bcell_tool"]).lower()
    if bt != "bepipred-3.0":
        raise ConfigError(f"epitopes.bcell_tool={bt!r}: only 'bepipred-3.0' is allowed "
                          "(BepiPred-2.0 scores AUC 0.574 on linear IEDB data)")
    eng = str(cfg["structure"]["engine"]).lower()
    if eng != "boltz-2":
        raise ConfigError(f"structure.engine={eng!r}: only 'boltz-2' is allowed "
                          "(AlphaFold 3 weights are CC-BY-NC-SA 4.0, non-commercial, by application)")
    bad = set(cfg["structure"]["refolder"]) - _KNOWN_REFOLDERS
    if bad:
        raise ConfigError(f"unknown structure.refolder entries: {sorted(bad)}")
    thr = cfg["thresholds"]["protegenicity"]
    if not (isinstance(thr, (list, tuple)) and len(thr) >= 2):
        raise ConfigError("thresholds.protegenicity must list at least two thresholds (always report both)")
    w = float(cfg["epitopes"]["mhc_class_ii_weight"])
    if not 0.0 <= w <= 1.0:
        raise ConfigError("epitopes.mhc_class_ii_weight must be in [0, 1]")
    if cfg["s4"]["on_unavailable"] not in ("carry", "fail"):
        raise ConfigError("s4.on_unavailable must be 'carry' or 'fail'")
    if cfg["s4"]["conservancy"]["on_fail"] not in ("reject", "flag"):
        raise ConfigError("s4.conservancy.on_fail must be 'reject' or 'flag'")
    if cfg["s7"]["boltz"]["msa"] not in ("server", "empty"):
        raise ConfigError("s7.boltz.msa must be 'server' or 'empty'")


def get(cfg: Mapping, dotted: str, default: Any = None) -> Any:
    cur: Any = cfg
    for part in dotted.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return default
        cur = cur[part]
    return cur
