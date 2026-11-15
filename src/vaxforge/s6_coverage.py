"""S6 - Population coverage and fairness.

Input : ``runs/<run_id>/s5_epitopes.json`` + an HLA allele-frequency table
        (``refdata.afnd_frequencies``, IEDB "user population" TSV format)
Output: ``runs/<run_id>/s6_coverage.json``

Reports per population: projected coverage, average epitope hits, PC90 and (when known) the
population sample size, for class I, class II and combined. The fairness block answers the
README's "interesting number": how much coverage is lost elsewhere if the epitope budget is
optimised on European frequencies only.

Limits (also written into the artifact): presentable != recognised != sufficient response !=
clinical protection; nothing here speaks to dose, adjuvant, durability or safety; HLA loci are
treated as independent although they are in linkage disequilibrium (our own assumption - we make
no claim about what the IEDB tool assumes).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .prediction import artifacts as art
from .prediction.cli import stage_main
from .prediction.config import coerce_config
from .prediction.errors import ContractError, ToolFailed, ToolUnavailable
from .prediction.popcov import (FrequencyTable, coverage_stats, export_iedb_epitope_allele_file,
                                fairness_report)
from .prediction.refdata import RefData
from .prediction.tools import tool_record

STAGE = "s6_coverage"
VERSION = 1

LIMITS = [
    "presentable != recognised != sufficient response != clinical protection",
    "says nothing about dose, adjuvant, durability or safety",
    "HLA loci are treated as independent (as the IEDB tool's own code does) although they are in strong linkage disequilibrium",
    "alleles missing from the frequency table contribute zero coverage (see unmapped_alleles)",
    "per-population sample sizes are reported only where the supplied table/companion file provides them",
]


def _slim_candidate(c: Mapping) -> dict:
    """Carry S5 evidence forward without the bulky epitope lists (full lists stay in S5 sidecars)."""
    out = {k: v for k, v in c.items() if k not in ("tcell", "bcell")}
    t = c["tcell"]
    out["tcell"] = {"score": t["score"], "score_basis": t["score_basis"], "all_epitopes_file": t["all_epitopes_file"],
                    "class_i": {k: v for k, v in t["class_i"].items() if k != "top"},
                    "class_ii": {k: v for k, v in t["class_ii"].items() if k != "top"}}
    b = c["bcell"]
    lin = b["linear"]
    out["bcell"] = {"linear_score": b["linear_score"],
                    "linear": {k: v for k, v in lin.items() if k != "regions"} | ({"regions": lin["regions"][:5]} if "regions" in lin else {}),
                    "conformational": b["conformational"]}
    return out


def run(target: Any, cfg: Any = None, *, s5_path: str | Path | None = None,
        table: FrequencyTable | None = None, refdata: RefData | None = None, force: bool = False, **_ignored) -> Path:
    """``target`` is a run directory or Stream A's ``RunStore`` (``run(store, config)``)."""
    cfg = coerce_config(cfg)
    store, run_dir = art.split_target(target)
    s5p = Path(s5_path) if s5_path else art.find_artifact(run_dir, "s5_epitopes")
    if s5p is None:
        raise ContractError(f"no s5_epitopes.json in {run_dir}")
    s5 = art.read_artifact(s5p)
    inputs = [art.input_ref(s5p, s5)]
    s6 = cfg["s6"]
    freq_path = cfg["refdata"]["afnd_frequencies"]
    params = {"s6": s6, "afnd_frequencies": str(freq_path) if freq_path else None,
              "afnd_sha256": art.file_sha256(freq_path) if freq_path and Path(freq_path).exists() else None,
              "afnd_sample_sizes": str(cfg["refdata"]["afnd_sample_sizes"]) if cfg["refdata"]["afnd_sample_sizes"] else None}
    if table is None and not force:
        if store is not None:
            if art.try_reuse_store(store, STAGE, params):
                return Path(store.artifact_path("s6"))
        else:
            reused = art.try_reuse(run_dir, STAGE, inputs, params)
            if reused:
                return reused

    warnings: list[str] = []
    tools: list[dict] = []
    cands = s5["payload"]["candidates"]
    ep_by_id = {e["epitope_id"]: e for e in s5["payload"]["epitope_set"]["t_cell"]}
    status: dict = {"status": "unavailable"}

    if table is None and not freq_path:
        try:                                              # cache-first (works offline once cached): IEDB-bundled AFND-derived frequencies
            rd = refdata or RefData(cfg["refdata"]["cache_dir"], offline=cfg["refdata"]["offline"])
            freq_path = rd.afnd_table(list(s6["regions"]))
            warnings.append(f"refdata.afnd_frequencies not set: built {freq_path} from the IEDB Population Coverage 3.0.2 bundle")
        except (ToolFailed, ToolUnavailable, OSError, ValueError) as e:
            status["reason"] = f"could not obtain HLA frequencies: {e}"
    if table is None:
        if not freq_path and "reason" not in status:
            status["reason"] = "refdata.afnd_frequencies is not set (HLA allele frequencies, IEDB user-population TSV format)"
        elif not freq_path:
            pass
        elif not Path(freq_path).exists():
            status["reason"] = f"allele frequency table not found: {freq_path}"
        else:
            table = FrequencyTable.from_tsv(freq_path, cfg["refdata"]["afnd_sample_sizes"])
    if not ep_by_id:
        status.setdefault("reason", "S5 produced no selected T-cell epitopes (T-cell prediction unavailable or no binders)")
        table_ok = False
    else:
        table_ok = table is not None
    if not table_ok and "reason" not in status:
        status["reason"] = "no allele frequency table"

    set_level: dict = {"n_epitopes": len(ep_by_id)}
    out_cands = []
    populations: list[str] = []
    if table_ok:
        wanted = [p for p in s6["regions"] if p in table.populations]
        populations = wanted or list(table.populations)
        if not wanted:
            warnings.append("none of s6.regions is a column of the frequency table; reporting every table population")
        epi_alleles = {eid: e["alleles"] for eid, e in ep_by_id.items()}
        modes = list(s6["modes"])
        by_pop = {}
        for pop in populations:
            by_pop[pop] = {m: coverage_stats(epi_alleles, table, pop, m).brief() for m in modes}
        set_level["by_population"] = by_pop
        k = min(int(s6["fairness"]["budget_k"]), len(epi_alleles))
        set_level["fairness"] = fairness_report(epi_alleles, table, reference_population=s6["fairness"]["reference_population"],
                                                populations=populations, k=k, mode=s6["fairness"]["mode"])
        if not set_level["fairness"].get("available"):
            warnings.append(f"fairness analysis skipped: {set_level['fairness'].get('reason')}")
        else:
            set_level["fairness_by_budget"] = {
                str(b): {kk: v for kk, v in fairness_report(epi_alleles, table, reference_population=s6["fairness"]["reference_population"],
                                                            populations=populations, k=min(int(b), len(epi_alleles)), mode=s6["fairness"]["mode"]).items()
                         if kk in ("budget_k", "rows", "mean_coverage_lost_excluding_reference", "max_coverage_lost")}
                for b in s6["fairness"]["budgets"]}
        sparse = sorted({pop for pop, r in by_pop.items() for m in r.values()
                         if m["min_locus_frequency_mass"] is not None and m["min_locus_frequency_mass"] < 0.8})
        if sparse:
            warnings.append(f"sparse allele-frequency data (a locus with epitope hits has summed frequency < 0.8) for: {sparse}; "
                            "their coverage is limited by missing data, not only by the epitope set")
            set_level["sparse_data_populations"] = sparse
        xcheck = export_iedb_epitope_allele_file(epi_alleles, run_dir / "data" / "s6" / "iedb_epitope_allele.tsv", table)
        set_level["iedb_crosscheck_file"] = {"file": art.run_file_ref(run_dir, xcheck),
                                             "use": "paste into the official IEDB Population Coverage tool to cross-check these numbers"}
        unmapped = sorted({a for r in by_pop.values() for m in r.values() for a in m["unmapped_alleles"]})
        if unmapped:
            warnings.append(f"{len(unmapped)} epitope-restricting alleles have no frequency data and contribute zero coverage: {unmapped[:8]}")
        if table.skipped_rows:
            warnings.append(f"{table.skipped_rows} rows of the frequency table had unparseable allele names and were skipped")
        score_pop = s6["score_population"] if s6["score_population"] in table.populations else None
        if score_pop is None:
            warnings.append(f"score_population {s6['score_population']!r} not in table; candidate score = mean over reported populations")
        status = {"status": "ran", "table": {"path": str(freq_path) if freq_path else "<injected>", "n_populations": len(table.populations),
                                             "n_alleles": len(table._index), "skipped_rows": table.skipped_rows,
                                             "sample_sizes_known": sum(1 for v in table.sample_sizes.values() if v is not None)},
                  "modes": modes, "populations": populations, "score_population": score_pop,
                  "independence_assumption": "loci treated as independent (own assumption; LD ignored)"}
        tools.append(tool_record("vaxforge-popcov (own implementation, IEDB Population Coverage method)", version="1",
                                 backend="python", fidelity="alternative",
                                 note="cross-check with the official IEDB tool via the exported epitope-allele file"))
        for c in cands:
            sel_ids = list(c["tcell"]["class_i"]["selected"] or []) + list(c["tcell"]["class_ii"]["selected"] or [])   # null = class did not run
            sub = {eid: ep_by_id[eid]["alleles"] for eid in sel_ids if eid in ep_by_id}
            slim = _slim_candidate(c)
            if not sub:
                slim["coverage"] = {"score": None, "reason": "no selected T-cell epitopes for this candidate", "n_selected_epitopes": 0}
            else:
                use_mode = "combined" if "combined" in modes else modes[0]
                per_pop = {p: coverage_stats(sub, table, p, use_mode) for p in populations}
                if score_pop:
                    score = per_pop[score_pop].coverage
                else:
                    score = sum(r.coverage for r in per_pop.values()) / len(per_pop)
                slim["coverage"] = {
                    "score": score, "score_population": score_pop or "mean_of_reported_populations", "mode_used": use_mode,
                    "n_selected_epitopes": len(sub),
                    "by_mode_at_score_population": ({m: coverage_stats(sub, table, score_pop, m).brief() for m in modes} if score_pop else None),
                    "by_population": {p: round(r.coverage, 6) for p, r in per_pop.items()},
                    "min_population": min(((p, r.coverage) for p, r in per_pop.items()), key=lambda x: x[1])[0],
                }
            out_cands.append(slim)
    else:
        warnings.append(f"population coverage did not run: {status['reason']}")
        for c in cands:
            slim = _slim_candidate(c)
            slim["coverage"] = {"score": None, "reason": status["reason"]}
            out_cands.append(slim)

    payload = {
        "context": s5["payload"]["context"],
        "analysis": status,
        "degraded": [] if status.get("status") == "ran" else [f"S6 population coverage did not run: {status.get('reason', 'no reason recorded')}"],
        "set_level": set_level,
        "candidates": out_cands,
        "epitope_set": s5["payload"]["epitope_set"],
        "analysis_status_s5": s5["payload"]["analysis_status"],
        "upstream": s5["payload"]["upstream"],
        "limits": LIMITS,
    }
    env = art.build_envelope(stage=STAGE, version=VERSION, run_id=run_dir.name, inputs=inputs, params=params, tools=tools,
                             warnings=warnings, payload=payload)
    return art.write_stage(target, env)


def main(argv=None) -> int:
    def extra(ap):
        ap.add_argument("--s5-path", dest="s5_path", type=Path, default=None)
    return stage_main(STAGE, run, argv, extra)


if __name__ == "__main__":
    raise SystemExit(main())
