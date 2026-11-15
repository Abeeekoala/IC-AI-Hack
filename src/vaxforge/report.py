"""``vaxforge report RUN_DIR [--out FILE]``: one self-contained HTML page of a run's evidence chain (stream D).  Without
``--out`` the page is written to ``RUN_DIR/report.html``, next to the artifacts it describes.

It reads the run only through ``RunStore`` and writes inline CSS, no script, no external font or image, so the file works
offline and prints.  Sections follow the team's Figma shell (top bar, title block, cards, "Honest ceiling" card, source
footer); a Warnings section is added between the pre-registration and the limits.

Honesty rules, enforced here and tested:
* A value that was not computed is a dash plus "not run" or "not applicable", never 0, empty or a default.
* A fixture run carries "Fixture, not a result" on the top bar and on every section.
* Every number sits next to its source stage and that stage's recorded tool versions.
* Every string that comes from an artifact is HTML-escaped; artifacts are data, never markup.
* Nothing is predicted or derived here.  S8, S9, S9+, S9.5, S10 and S12 show only the fields named in
  docs/design/screen_fields.md, as the artifact states them (counts and one percentage are the only arithmetic); a field
  this report does not name is not shown, and a stage with nothing in it says "not run".

The sub-command is registered through ``register_cli`` (cli.py must list ``report`` in EXTENSION_MODULES);
``python -m vaxforge.report RUN_DIR`` works without that.
"""
from __future__ import annotations

import argparse
import html
import sys
from collections import Counter
from pathlib import Path

from .artifacts import STAGE_ORDER, ContractError, RunStore
from .cache import sha256_file, utcnow_iso

TITLES = {"s0": "S0 · Taxonomy resolution", "s1": "S1 · Data acquisition", "s2": "S2 · Virulence and localisation",
          "s3": "S3 · Antigenicity ranking", "s4": "S4 · Safety and conservancy filters", "s5": "S5 · Epitope prediction",
          "s6": "S6 · Population coverage", "s7": "S7 · Structure and final ranking", "s8": "S8 · Protocol generation",
          "s9": "S9 · Robot code", "s9plus": "S9+ · Protocol animation (optional)", "s9_5": "S9.5 · Fine-tuned protocol code",
          "s10": "S10 · Animal study design", "s11": "S11 · Pre-registration", "s12": "S12 · Biosecurity gate"}
BASIS = ("s7", "s6", "s5", "s4", "s3")          # the latest stage that lists candidates is the one ranked on the page
PAYLOAD_STATES = (("not_run", "not run"), ("not_screened", "not run"), ("blocked", "blocked"), ("refus", "refused"))
AUTOMATION = {"full": "ROBOT (full)", "partial": "ROBOT-PARTIAL (partial)", "manual": "HUMAN (manual)"}
FIXTURE = '<span class="fixture">Fixture, not a result</span>'

# Fixed text, copied from README sections 4 and 7 (tests/test_report.py fails if the README changes and this does not).
CEILINGS = (
    ("B-cell epitope prediction",
     "BepiPred-2.0 AUC 0.574; field ceiling ~0.70–0.84"),
    ("Antigenicity (VaxiJen)",
     "AUC 0.56, MCC 0.08; flags ~64% of proteome"),
    ("Blind docking (ClusPro)",
     "13.7% top-1, 45.1% top-10"),
    ("Affinity prediction (Boltz-2)",
     "Pearson 0.62 vs FEP+"),
    ("Solubility prediction",
     "Best of 12 predictors: 58.4% accuracy (AUC 0.60, MCC 0.17). Value is enrichment — top 10% of SoluProt rankings yielded 199 true positives vs 145 expected by chance, a 37.2% relative lift — not replacing the experiment"),
    ("De novo binder design",
     "BindCraft reports 46.3% mean success (10–100% by target). An independent reproduction of RFdiffusion found near-total failure on several targets, with no measurable affinity. Design success is target-dependent and often much worse than headline numbers"),
    ("Mouse immunogenicity → human protection",
     "No reliable predictive relationship. Most vaccines lack an agreed correlate of protection; meningococcal hSBA is an exception, not the rule"),
    ("Expression itself",
     "ProteinGenerator designs intended to be soluble monomers passed 32/42; 8/19 when tryptophan-enriched. \"It expresses\" is itself a high-failure experimental outcome"),
    ("Trial outcome prediction",
     "HINT reports PR-AUC 0.772/0.607/0.623 (Ph 1/2/3) on its own benchmark; on an independent evaluation of 2,163 trials its balanced accuracy was 0.470–0.594 with overall MCC 0.050. In the same evaluation GPT-4o-mini degenerated to predicting \"success\" for everything (balanced accuracy 0.500, recall 1.000, specificity 0.000)"),
    ("Vaccine industry base rate",
     "Phase 1 → approval 39.6% (industry-sponsored), 6.8% (non-industry). The narrowest transition is Phase 2 → 3 at 65.4% — exactly the point where immunogenicity data exists but efficacy data does not, which is the furthest any in-silico immune model can reach"),
    ("Fine-tuning for protocol code (S9.5)",
     "Improves syntactic and procedural conformance to the Opentrons API and to protocol conventions. Does not improve biological validity: a protocol that compiles and passes simulation can still ruin an experiment, and the win rate inherits that limit"),
)

ABSENT = (
    ("Phase 1/2/3 trial simulation",
     "No scientific or regulatory basis for vaccines (§0)"),
    ("C-ImmSim immune simulation",
     "Model described only partially in the literature, no complete manual, derivative IMMSIM++ shipped as binary only so third parties cannot test it. Widely used in this genre; treated here as decorative"),
    ("MD + MM-PBSA \"validation\"",
     "Flagged in the literature as computational validation that does not demonstrate immunological efficacy"),
    ("ClusPro–TLR docking",
     "See §S7"),
    ("Predicted assay readouts",
     "See §0"),
)

CSS = """
:root{--bg:#f6f6f3;--card:#fff;--ink:#1b1f24;--mute:#59626e;--line:#d6d9de;--link:#164e87;--ceil-bg:#fff6dc;--ceil-line:#b7791f;
--fix-bg:#ffe08a;--fix-ink:#4a3200;--done:#1c6b3b;--done-bg:#e2f3e8;--nr:#525a66;--nr-bg:#e8eaee;--warn:#7a4a00;--warn-bg:#fdecc8;
--bad:#8e1b1b;--bad-bg:#fbe3e3;--info:#164e87;--info-bg:#e0ecf8;--block:#5b2a86;--block-bg:#eee3f8}
@media screen and (prefers-color-scheme:dark){:root{--bg:#0f1318;--card:#171c23;--ink:#e7eaee;--mute:#a3acb8;--line:#2e3641;--link:#8dbdf0;
--ceil-bg:#2a2412;--ceil-line:#c9962f;--fix-bg:#5c4400;--fix-ink:#ffe9b0;--done:#7fd39a;--done-bg:#12301d;--nr:#b4bcc7;--nr-bg:#262d37;
--warn:#f0c77a;--warn-bg:#35280d;--bad:#f2a1a1;--bad-bg:#3a1717;--info:#8dbdf0;--info-bg:#14283f;--block:#cfaef0;--block-bg:#2d1d42}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main,.top,footer{max-width:62rem;margin:0 auto;padding:0 16px}
a{color:var(--link)}code{font-size:.85em;overflow-wrap:anywhere}
.top{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;justify-content:space-between;padding-top:12px;padding-bottom:12px;border-bottom:1px solid var(--line)}
h1{font-size:1.6rem;margin:1.2rem 0 .3rem}h2{font-size:1.25rem;margin:0}h3{font-size:1rem;margin:1rem 0 .4rem}
section{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px 16px;margin:16px 0}
section>header{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;margin-bottom:8px}
.fixture{background:var(--fix-bg);color:var(--fix-ink);font-weight:700;padding:2px 10px;border-radius:99px;font-size:.8rem}
.chip{display:inline-block;padding:1px 10px;border-radius:99px;font-size:.8rem;font-weight:600;border:1px solid currentColor}
.chip-done{color:var(--done);background:var(--done-bg)}.chip-not-run{color:var(--nr);background:var(--nr-bg)}
.chip-needs-user-input,.chip-fixture{color:var(--warn);background:var(--warn-bg)}.chip-failed{color:var(--bad);background:var(--bad-bg)}
.chip-refused{color:var(--bad);background:var(--bad-bg);border-style:dashed}.chip-blocked{color:var(--block);background:var(--block-bg)}
.chip-cached,.chip-review{color:var(--info);background:var(--info-bg)}
.nr{color:var(--mute);font-style:italic}.meta,.src,small{color:var(--mute);font-size:.8rem}
nav ol{columns:2 14rem;margin:.5rem 0;padding-left:1.4rem}
.cards{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(15rem,1fr));gap:10px}
.card{border:1px solid var(--line);border-radius:6px;padding:8px 12px}.card h3{margin:0 0 .3rem}.card p{margin:.3rem 0}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:.9rem}
th,td{text-align:left;vertical-align:top;padding:4px 8px;border-bottom:1px solid var(--line);overflow-wrap:anywhere}
.cand{border-top:1px solid var(--line);margin-top:12px}
.ceiling{background:var(--ceil-bg);border-left:4px solid var(--ceil-line);border-radius:4px;padding:6px 12px;margin-top:12px}
.ceiling h3{margin:.2rem 0}.ceiling p{margin:.2rem 0 .4rem}
.review{background:var(--info-bg);border-left:4px solid var(--info);border-radius:4px;padding:6px 12px;margin:12px 0}
.review h3{margin:.2rem 0}.review ul{margin:.2rem 0 .4rem}td ul{margin:0;padding-left:1.1rem}
footer{padding-bottom:32px}footer h2{margin:1rem 0 .5rem}
@media print{nav{display:none}body{background:#fff;color:#000;font-size:10pt}section,.card,.cand,.review{break-inside:avoid}a{color:inherit}}
"""


# ---- small helpers ---------------------------------------------------------------------------------------------
class H(str):
    """Text that is already escaped HTML."""


NA = object()      # marks "not applicable" (None means "not run")


def e(x) -> str:
    return html.escape(str(x), quote=True)


def dash(kind: str = "not run") -> H:
    return H(f'<span class="nr">&mdash; {kind}</span>')


def fmt(v) -> str:
    """HTML for one artifact value.  None and "" are not values: they render as a dash, never as 0 or empty."""
    if isinstance(v, H):
        return v
    if v is NA:
        return dash("not applicable")
    if v is None or v == "":
        return dash()
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.4g}"
    if isinstance(v, (list, tuple)):
        return ", ".join(fmt(i) for i in v) or "none"
    return e(v)


def dig(d, *path):
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def dd(x) -> dict:
    return x if isinstance(x, dict) else {}


def rows_of(x) -> list[dict]:
    return [i for i in x if isinstance(i, dict)] if isinstance(x, list) else []


def tools_text(t) -> str:
    return ", ".join(f"{k} {v}" for k, v in t.items()) if isinstance(t, dict) and t else "tool version not recorded"


def tbl(head, rows) -> str:
    """``rows`` hold cells that are already safe HTML."""
    th = "".join(f'<th scope="col">{e(h)}</th>' for h in head)
    tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>'


class Run:
    """The run as RunStore reads it: verified artifacts, the reason an unreadable one failed, the candidate lists."""

    def __init__(self, run_dir):
        self.store = RunStore(run_dir)
        self.arts, self.errors = {}, {}
        for s in self.store.stages_present():
            try:
                self.arts[s] = self.store.read_artifact(s)
            except ContractError as exc:
                self.errors[s] = str(exc)
        self.manifest = self.store.read_manifest()
        self.run_id = self.manifest.get("run_id") or self.store.run_id
        self.fixture = ("fixture" in f"{self.run_id} {self.store.run_id}".lower()
                        or (self.store.run_dir / "FIXTURE.md").is_file()
                        or any(str(w).startswith("FIXTURE") for a in self.arts.values() for w in a.warnings))
        self.idx = {s: self.index(s, *keys) for s, keys in (("s2", ("proteins",)), ("s3", ("candidates", "ranking")),
                                                          ("s4", ("candidates",)), ("s5", ("candidates",)),
                                                          ("s6", ("candidates",)), ("s7", ("candidates",)))}
        self.basis, self.cands = None, []
        for s in BASIS:
            if rows_of(dig(self.payload(s), "candidates")):
                self.basis, self.cands = s, rows_of(self.payload(s)["candidates"])
                break

    def ok(self, s) -> bool:
        return s in self.arts and self.arts[s].status == "ok"

    def payload(self, s) -> dict:
        return self.arts[s].payload if self.ok(s) else {}

    def index(self, s, *keys) -> dict:
        out = {}
        for k in keys:
            for c in rows_of(dig(self.payload(s), k)):
                out.setdefault(c.get("protein_id"), c)
        return out

    def src(self, s) -> str:
        a = self.arts.get(s)
        return f"{s} · no artifact" if a is None else f"{s} · {tools_text(a.producer.get('tools'))}"

    def state(self, s) -> str:
        """Exactly one of: done, not run, needs user input, failed, blocked, refused, cached, fixture."""
        if s in self.errors:
            return "failed"
        a = self.arts.get(s)
        if a is None:
            return "not run"
        if a.status == "failed":
            return "failed"
        if a.status == "needs_user_input":
            return "needs user input"
        for path in (("status",), ("fine_tune", "status"), ("decision",), ("state",)):
            word = str(dig(a.payload, *path)).lower()
            for prefix, label in PAYLOAD_STATES:
                if word.startswith(prefix):
                    return label
        if self.fixture:
            return "fixture"
        hits = a.volatile.get("cache_hits")
        return "cached" if isinstance(hits, dict) and hits and all(hits.values()) else "done"

    def why(self, s) -> str:
        if s in self.errors:
            return "Artifact could not be read: " + self.errors[s]
        a = self.arts.get(s)
        if a is None:
            return "No artifact in this run."
        for path in (("resolution", "message"), ("fine_tune", "reasons"), ("reason",), ("reasons",)):
            v = dig(a.payload, *path)
            if v:
                return "; ".join(map(str, v)) if isinstance(v, list) else str(v)
        if self.state(s) in ("done", "cached", "fixture"):
            return ""
        return a.warnings[0] if a.warnings else "No details recorded."


def chip(state: str) -> str:
    return f'<span class="chip chip-{state.replace(" ", "-")}">{state}</span>'


def ev(r: Run, rows) -> str:
    """Evidence table: (label, value, stage) -> label, value, and the stage and tool versions that produced it."""
    return tbl(("Evidence", "Value", "Source"), [(e(label), fmt(v), e(r.src(s))) for label, v, s in rows])


def missing(r: Run, s: str) -> H:
    st = r.state(s)
    return H(str(dash()) + ("" if st == "not run" else f" ({st})"))


def stage_block(r: Run, s: str, extra: str | None = None) -> str:
    a = r.arts.get(s)
    out = f"<h3>{e(TITLES[s])} {chip(r.state(s))}</h3><p>{e(r.why(s))}</p>"
    if a is None:
        return out if s in r.errors else out + f"<p>{dash()} no artifact for this stage in this run.</p>"
    if extra is None:
        keys = ", ".join(sorted(map(str, a.payload)))
        extra = (f"<p>Payload fields: <code>{e(keys)}</code>. This report does not render the contents of this stage; "
                 f"read the artifact itself.</p>")
    return out + f"<p>Artifact <code>{e(a.path.name)}</code>, digest <code>{e(a.digest)}</code>.</p>" + extra


# ---- sections --------------------------------------------------------------------------------------------------
def launcher(r: Run) -> str:
    s0, s1 = r.payload("s0"), r.payload("s1")
    res = dd(s0.get("resolution"))
    rows = [("Pathogen as typed", s0.get("query"), "s0"), ("Resolution", res.get("message"), "s0"),
            ("Resolved taxon", dig(s0, "taxon", "scientific_name"), "s0"), ("NCBI taxid", dig(s0, "taxon", "taxid"), "s0"),
            ("Matched by", res.get("method"), "s0"), ("Organism domain", dig(s0, "organism", "domain"), "s0"),
            ("Gram stain", dig(s0, "organism", "gram_stain"), "s0"), ("Proteome", dig(s1, "proteome", "name"), "s1"),
            ("Proteome id", dig(s1, "proteome", "upid"), "s1"), ("Proteins in proteome file", dig(s1, "proteome", "n_sequences"), "s1"),
            ("UniProt release", dig(s1, "proteome", "release"), "s1"),
            ("Candidates carried into S7 (top-k)", r.payload("s7").get("top_k"), "s7")]
    return "<p>The inputs and settings this run recorded. This report does not start runs.</p>" + ev(r, rows)


def overview(r: Run) -> str:
    cards = []
    for s in STAGE_ORDER:
        a = r.arts.get(s)
        meta = "no artifact" if a is None else f"{e(r.src(s))}<br>digest {e(a.digest[:23])}…<br>{len(a.warnings)} warning(s)"
        cards.append(f'<li class="card"><h3>{e(TITLES[s])}</h3>{chip(r.state(s))}<p>{e(r.why(s))}</p><p class="meta">{meta}</p></li>')
    n_warn = sum(len(a.warnings) for a in r.arts.values())
    return (f'<p>{len(r.arts)} of {len(STAGE_ORDER)} stages have a readable artifact; {n_warn} warning(s) in total '
            f'(<a href="#warnings">all warnings</a>).</p><ul class="cards">{"".join(cards)}</ul>')


def ranked(r: Run) -> str:
    if not r.cands:
        return f"<p>{dash()} no stage from S3 to S7 recorded a candidate list in this run.</p>"
    top = r.basis == "s7"
    stage = "s7" if top else "s3"
    ranker = dd(r.payload("s3").get("ranker"))
    out = [f"<p>Candidates are listed as the <code>{r.basis}</code> artifact records them"
           f"{'' if top else ' (S7 has not produced the final ranking, so the S3 rank is shown)'}. "
           f"S3 ranker backend: {fmt(ranker.get('backend'))}. {e(ranker.get('score_semantics') or '')}</p>"]
    rows = []
    for n, c in enumerate(r.cands, 1):
        pid = c.get("protein_id")
        s3 = r.idx["s3"].get(pid, {})
        rank, score = (c.get("rank"), dig(c, "scores", "final")) if top else (s3.get("rank"), s3.get("composite_score"))
        notes = [f"S4 flag: {f}" for f in r.idx["s4"].get(pid, {}).get("flags") or []]
        notes += ["outside the top-k: no structure step"] if top and c.get("in_top_k") is False else []
        rows.append((fmt(rank), f'<a href="#c{n}">{e(c.get("name") or pid)}</a><br><small>{e(pid)}</small>', fmt(score),
                     f'<span class="src">{e(r.src(stage))}</span>', e("; ".join(notes))))
    out.append(tbl(("Rank", "Protein", "Final score" if top else "Composite score", "Source", "Notes"), rows))
    rej = rows_of(r.payload("s4").get("rejected"))
    if rej:
        out.append("<h3>Rejected by S4 (kept so they can be recovered and inspected)</h3>" + tbl(
            ("Protein", "Rejected by", "Recoverable", "Source"),
            [(f'{e(c.get("name") or c.get("protein_id"))}<br><small>{e(c.get("protein_id"))}</small>', fmt(c.get("rejected_by")),
              fmt(c.get("recoverable")), f'<span class="src">{e(r.src("s4"))}</span>') for c in rej]))
    return "".join(out)


def localisation(rec) -> object:
    v = dig(rec, "localisation")
    if not isinstance(v, dict) or v.get("status") == "not_run":
        return None
    if v.get("status") == "not_applicable":
        return NA
    return f"{v.get('label')} ({v.get('status')}; accessibility {v.get('accessibility')})"


def virulence(rec) -> object:
    v = dig(rec, "virulence")
    if not isinstance(v, dict) or v.get("status") == "not_run":
        return None
    if v.get("status") == "not_applicable":
        return NA
    if v.get("status") == "no_hit":
        return "no qualifying VFDB hit"
    if v.get("status") != "hit":
        return f"status {v.get('status')}"
    b = dd(v.get("best_hit"))
    return H(f"hit {e(b.get('vfdb_id'))} {e(b.get('gene'))}: identity {fmt(b.get('identity_pct'))}%, query coverage "
             f"{fmt(b.get('query_coverage_pct'))}%, E-value {fmt(b.get('evalue'))}; evidence {e(v.get('evidence'))}")


def evidence_rows(r: Run, pid) -> list:
    g = lambda s: r.idx[s].get(pid, {})  # noqa: E731
    rows = []
    if r.ok("s2"):
        rows += [("Localisation", localisation(g("s2")), "s2"), ("Virulence (BLAST vs VFDB)", virulence(g("s2")), "s2")]
    if r.ok("s3"):
        c = g("s3")
        rows += [("Rank", c.get("rank"), "s3"), ("Protegenicity", c.get("protegenicity"), "s3"),
                 ("Composite score", c.get("composite_score"), "s3")]
        rows += [(f"Passes protegenicity threshold {t}", v, "s3") for t, v in dd(c.get("passes_thresholds")).items()]
    else:
        rows.append(("S3 antigenicity ranking", missing(r, "s3"), "s3"))
    if r.ok("s4"):
        c = g("s4")
        for name, gate in dd(c.get("gates")).items():
            gate = dd(gate)
            val = (H(f"{dash()} {e(gate.get('reason'))}") if gate.get("status") == "unavailable"
                   else f"{gate.get('status')}: {gate.get('reason')}")
            rows.append((f"S4 gate {name}", val, "s4"))
        if not dd(c.get("gates")):
            rows.append(("S4 gates", None, "s4"))
        rows += [(label, c[k], "s4") for label, k in (("S4 flags", "flags"), ("S4 rescued despite", "rescued_despite")) if c.get(k)]
    else:
        rows.append(("S4 safety and conservancy gates", missing(r, "s4"), "s4"))
    if r.ok("s5"):
        c = g("s5")
        rows += [("T-cell score (heuristic, unvalidated)", dig(c, "tcell", "score"), "s5"),
                 ("Class I epitopes", dig(c, "tcell", "class_i", "n_epitopes"), "s5"),
                 ("Class I promiscuous epitopes", dig(c, "tcell", "class_i", "n_promiscuous"), "s5"),
                 ("Class II epitopes", dig(c, "tcell", "class_ii", "n_epitopes"), "s5"),
                 ("B-cell linear score (heuristic, unvalidated)", dig(c, "bcell", "linear_score"), "s5"),
                 ("Conformational epitope status", dig(c, "bcell", "conformational", "status"), "s5")]
    else:
        rows.append(("S5 epitope prediction", missing(r, "s5"), "s5"))
    if r.ok("s6"):
        c = g("s6")
        rows += [(f"Population coverage score ({dig(c, 'coverage', 'score_population')}, {dig(c, 'coverage', 'mode_used')})",
                  dig(c, "coverage", "score"), "s6"), ("Lowest-coverage population", dig(c, "coverage", "min_population"), "s6")]
    else:
        rows.append(("S6 population coverage", missing(r, "s6"), "s6"))
    if r.ok("s7"):
        c = g("s7")
        rows += [("Final score (weights are unvalidated priors)", dig(c, "scores", "final"), "s7"),
                 ("Rank basis", c.get("rank_basis"), "s7"), ("Structure status", dig(c, "structure", "status"), "s7"),
                 ("Mean pLDDT (confidence of the model, not immunogenicity)", dig(c, "structure", "mean_plddt"), "s7"),
                 ("Validator agreement", dig(c, "structure", "validator_agreement", "value"), "s7")]
    else:
        rows.append(("S7 structure and final ranking", missing(r, "s7"), "s7"))
    return rows


def detail(r: Run) -> str:
    if not r.cands:
        return f"<p>{dash()} no candidates to show evidence for.</p>"
    return "".join(f'<article class="cand" id="c{n}"><h3>{n}. {e(c.get("name") or c.get("protein_id"))} '
                   f'<small>{e(c.get("protein_id"))}</small></h3>{ev(r, evidence_rows(r, c.get("protein_id")))}</article>'
                   for n, c in enumerate(r.cands, 1))


def validation(r: Run) -> str:
    p = r.payload("s3")
    audit = dd(p.get("leakage_audit"))
    holdout = audit.get("holdout_enforced")
    rows = [("Ranker backend", dig(p, "ranker", "backend"), "s3"), ("Model", audit.get("model"), "s3"),
            ("Holdout enforced", H(f"{dash()} (declared, not verified)") if holdout is None and audit else holdout, "s3"),
            ("Declared holdout taxids", audit.get("declared_holdout_taxids"), "s3"), ("Leakage note", audit.get("note"), "s3"),
            ("Fold enrichment", dig(p, "counts", "fold_enrichment"), "s3"), ("Fold enrichment note", dig(p, "counts", "fold_enrichment_note"), "s3")]
    vdir = r.store.run_dir / "validation"
    files = sorted(f.relative_to(vdir).as_posix() for f in vdir.rglob("*") if f.is_file()) if vdir.is_dir() else []
    listing = ("<ul>" + "".join(f"<li><code>{e(f)}</code></li>" for f in files) + "</ul>") if files else f"<p>{dash()} no files under <code>validation/</code>.</p>"
    return (ev(r, rows) + "<h3>Validation outputs in this run</h3>" + listing +
            "<p>LOPOV, the Bexsero recall@3 case and the leakage check write under <code>validation/</code>. "
            "This report lists those files and does not read them.</p>")


def prereg(r: Run) -> str:
    extra = None
    if r.ok("s11"):
        p = r.payload("s11")
        f = dd(p.get("frozen"))
        rows = [("Registered at (this machine's clock)", f.get("registered_at"), "s11"), ("Frozen digest", p.get("frozen_digest"), "s11"),
                ("Config digest", f.get("config_digest"), "s11")]
        up = [(e(k), fmt(dd(v).get("status")), fmt(dd(v).get("digest")), e(tools_text(dd(v).get("tools")))) for k, v in dd(f.get("upstream")).items()]
        extra = (ev(r, rows) + (f"<p>{e(p['note'])}</p>" if p.get("note") else "") + "<h3>Upstream artifacts frozen</h3>"
                 + tbl(("Stage", "Status when frozen", "Artifact digest", "Tools"), up))
    return stage_block(r, "s11", extra) + stage_block(r, "s12", gate(r))


# ---- S8, S9, S9+, S9.5, S10, S12: the fields docs/design/screen_fields.md names, as each artifact states them ----------------
def listed(xs) -> object:
    """A list of strings as a bullet list; an empty or missing list is a dash, not "none"."""
    items = [x for x in xs if x not in (None, "")] if isinstance(xs, list) else []
    return H("<ul>" + "".join(f"<li>{fmt(x)}</li>" for x in items) + "</ul>") if items else dash()


def counts_text(counts) -> object:
    """``{"PASS": 12, "FAIL": 1}`` as "PASS 12, FAIL 1"; a missing or empty mapping is a dash."""
    pairs = [(k, v) for k, v in counts.items()] if isinstance(counts, dict) else []
    return H(", ".join(f"{e(k)} {fmt(v)}" for k, v in pairs)) if pairs else dash()


def real_count(r: Run, s: str, n):
    """A stage that did not run writes 0 for its counts; that 0 is "nothing was done", not a result, so it shows as a dash."""
    return None if n == 0 and r.state(s) == "not run" else n


def no_content(r: Run, s: str, what: str) -> str:
    return f"<p>{missing(r, s)} {e(what)}</p>"


def cost_text(cost) -> object:
    """The S8 cost as the artifact states it.  No price is ever invented: a missing total is "not estimated"."""
    c = dd(cost)
    status = c.get("status")
    # an amount is shown exactly as the artifact states it: fmt() would round money to four significant digits
    money = lambda t: H(f"{e(dd(t).get('currency'))} {dash() if dd(t).get('amount') is None else e(dd(t).get('amount'))}")  # noqa: E731
    if not c:
        return None
    if status == "estimated" and c.get("total"):
        return H(f"estimated total {money(c['total'])} (only the listed items are counted)")
    if status == "partial":
        priced = f"; priced so far {money(c['priced_subtotal'])}" if c.get("priced_subtotal") else ""
        return H(f"{dash('partial, no total')}{priced}")
    return dash("not estimated")


def protocol(r: Run) -> str:
    p = r.payload("s8")
    cands = rows_of(p.get("candidates"))
    if not cands:
        return no_content(r, "s8", "no protocol: this run has no S8 candidates.")
    fasta = p.get("order_fasta")
    order = (H(f"{fmt(dd(fasta).get('records'))} record(s) in <code>{e(dd(fasta).get('path'))}</code>; each sequence must clear S12 "
               f"before anything is ordered") if isinstance(fasta, dict) else None)
    out = [ev(r, [("Candidates with a protocol", len(cands), "s8"), ("Candidates chosen from", dig(p, "selection", "source"), "s8"),
                  ("Cost (all shortlisted protocols)", cost_text(p.get("cost")), "s8"), ("Order list for gene synthesis", order, "s8")])]
    if dig(p, "cost", "note"):
        out.append(f"<p>{e(dig(p, 'cost', 'note'))}</p>")
    for n, c in enumerate(cands, 1):
        pr = dd(c.get("protocol"))
        steps = Counter(str(s.get("automation")) for s in rows_of(pr.get("steps")))
        step_text = (H(f"{sum(steps.values())} steps: " + ", ".join(f"{AUTOMATION.get(k) or e(k)} {v}" for k, v in steps.items()))
                     if steps else None)
        limit = dig(pr, "config", "endotoxin_limit_eu_per_ug")
        rows = [("Rank", c.get("rank"), "s8"), ("Rank source", c.get("rank_source"), "s8"),
                ("Falsification condition", pr.get("falsifier"), "s8"), ("What this does NOT measure", listed(pr.get("does_not_measure")), "s8"),
                ("Steps and who does them", step_text, "s8"), ("Readouts", listed(dig(pr, "config", "readouts")), "s8"),
                ("Endotoxin limit (EU/ug)", dash("not set") if limit is None else limit, "s8"),
                ("Cost range (GBP)", dash("not estimated") if pr.get("cost_gbp_range") is None else pr.get("cost_gbp_range"), "s8"),
                ("Estimate basis", pr.get("estimate_basis"), "s8")]
        out.append(f'<article class="cand"><h3>{n}. {e(c.get("protein_name") or c.get("candidate_id"))} '
                   f'<small>{e(c.get("candidate_id"))}</small></h3>{ev(r, rows)}</article>')
    return "".join(out)


def robot(r: Run) -> str:
    p = r.payload("s9")
    cands = rows_of(p.get("candidates"))
    sim = dd(p.get("simulator"))
    version = sim.get("opentrons_version")
    simulator = (H(f"Opentrons {e(version)}, simulated") if sim.get("simulated") is True and version
                 else H("simulated, version not recorded") if sim.get("simulated") is True else dash("not simulated"))
    out = [ev(r, [("Simulator", simulator, "s9"), ("Sessions (one robot-code file each)", real_count(r, "s9", dig(p, "summary", "n_sessions")), "s9"),
                  ("Outcomes of all sessions", counts_text(dig(p, "summary", "outcomes")), "s9"),
                  ("Parameters", p.get("parameters_status"), "s9"), ("Biosecurity", p.get("biosecurity"), "s9")])]
    rows, failing = [], []
    for c in cands:
        for b in rows_of(c.get("backends")) or [{}]:
            sessions = rows_of(b.get("sessions"))
            tally = Counter(str(s.get("outcome")) for s in sessions)
            other = {k: v for k, v in tally.items() if k not in ("PASS", "FAIL", "UNVERIFIED")}
            # a stage that did not run has no PASS to show, whatever a session says
            passed = None if r.state("s9") == "not run" else tally["PASS"]
            cells = ([len(sessions), passed, tally["FAIL"], tally["UNVERIFIED"], counts_text(other) if other else "none"]
                     if sessions else [None] * 5)
            rows.append((f'{e(c.get("protein_name") or c.get("candidate_id"))}<br><small>{e(c.get("candidate_id"))}</small>',
                         fmt(b.get("label")), *map(fmt, cells), f'<span class="src">{e(r.src("s9"))}</span>'))
            failing += [(c, b, s) for s in sessions if s.get("outcome") != "PASS"]
    if rows:
        out.append("<h3>Simulator verdicts per candidate and code backend</h3>" + tbl(
            ("Candidate", "Backend", "Sessions", "PASS", "FAIL", "UNVERIFIED", "Other", "Source"), rows))
    else:
        out.append(no_content(r, "s9", "no robot code in this run."))
    if failing:
        why = lambda s: dd(s.get("refusal")).get("detail") or s.get("error") or dd(s.get("grade")).get("sim_error")  # noqa: E731
        out.append("<h3>Sessions that did not pass</h3>" + tbl(
            ("Candidate", "Backend", "Session", "Outcome", "Reason"),
            [(e(c.get("candidate_id")), fmt(b.get("label")), e(s.get("session_id")), fmt(s.get("outcome")), fmt(why(s) or dash("not recorded"))) for c, b, s in failing]))
    return "".join(out)


def animation(r: Run) -> str:
    s = dd(r.payload("s9plus").get("summary"))
    rows = [("Animations written", real_count(r, "s9plus", s.get("n_animated")), "s9plus"),
            ("Sessions considered", real_count(r, "s9plus", s.get("n_sessions")), "s9plus"),
            ("Sessions skipped", real_count(r, "s9plus", s.get("n_skipped")), "s9plus")]
    note = ("<p>The animations are separate HTML files in the run folder (<code>data/s9plus/</code>); this report does not embed them. "
            "Their times are estimates, not measurements.</p>")
    return ev(r, rows) + (note if r.state("s9plus") != "not run" else "")


def ref_text(ref) -> str:
    """Where a sidecar file is and its recorded hash, from a contract file reference."""
    ref = dd(ref)
    if not ref.get("path"):
        return "file reference not recorded"
    return f"{ref['path']} · sha256 {str(ref.get('sha256'))[:16]}…"


def adaption(r: Run, ft: dict) -> str:
    """The Adaption run facts and the training-manifest counts that the S9.5 stage copied into its artifact."""
    out = ["<h3>Adaption run (Adaption's own numbers about its own training run)</h3>"]
    a = ft.get("adaption")
    if not isinstance(a, dict):
        return out[0] + f"<p>{dash()} no Adaption evidence is attached to this run (set <code>finetune.evidence</code> and re-run S9.5).</p>"
    where = f"{r.src('s9_5')}; file {ref_text(ft.get('evidence'))}"
    best = H(f"{fmt(a.get('best_win_rate'))}<br><small>Adaption's own optimisation target. "
             + (e(a.get("caveat")) if a.get("caveat") else "The artifact carries no caveat; read this number as an optimisation target, not as protocol quality.")
             + "</small>")
    rows = [("Adaption run id", a.get("run_id")), ("Base model (pinned)", a.get("base_model_id")),
            ("Trained model name", a.get("trained_model_name")), ("Run status (as Adaption reports it)", a.get("status")),
            ("Training method", a.get("training_method")), ("Training type", a.get("training_type")),
            ("Iterations completed (of the cap)", H(f"{fmt(a.get('iterations_completed'))} of {fmt(a.get('max_iterations'))}")),
            ("best_win_rate, Adaption's own number", best), ("Target win rate set for the run", a.get("target_win_rate")),
            ("Target reached (Adaption's own comparison)", dash("not computed") if a.get("target_reached") is None else a.get("target_reached")),
            ("Checkpoint sha256", dash("not downloaded") if a.get("checkpoint_sha256") is None else a.get("checkpoint_sha256")),
            ("Completed at (UTC)", a.get("completed_at_utc"))]
    out.append(tbl(("Evidence", "Value", "Source"), [(e(k), fmt(v), e(where)) for k, v in rows]))
    out.append("<h3>Held-out win rate (this project's own evaluation, <code>validation/winrate.py</code>)</h3>")
    out.append(ev(r, [("Held-out win rate", ft.get("held_out_win_rate"), "s9_5")]))
    c = ft.get("manifest_counts")
    out.append("<h3>Training manifest</h3>")
    if not isinstance(c, dict):
        return "".join(out) + f"<p>{dash()} no training manifest is attached (set <code>finetune.manifest</code> and re-run S9.5).</p>"
    n, syn = c.get("included"), c.get("included_synthetic")
    ints = all(isinstance(x, int) and not isinstance(x, bool) for x in (n, syn)) and n > 0
    share = H(f"{syn} of {n} ({100 * syn / n:.1f}%), computed here from the two counts") if ints else dash("not computed")
    where = f"{r.src('s9_5')}; file {ref_text(ft.get('manifest'))}"
    rows = [("Rows included in the training file", n), ("Candidate rows considered", c.get("candidates")),
            ("Synthetic rows among those included", share), ("Meets the row minimum", c.get("meets_min_rows")),
            ("Longest row allowed (characters)", c.get("max_row_chars"))]
    return "".join(out) + tbl(("Evidence", "Value", "Source"), [(e(k), fmt(v), e(where)) for k, v in rows])


def finetune(r: Run) -> str:
    p = r.payload("s9_5")
    ft = dd(p.get("fine_tune"))
    status = str(ft.get("status"))
    shown = (dash("not run") if status.startswith("not_run") else "evidence attached (this stage trained and evaluated nothing itself)"
             if status == "evidence_attached" else ft.get("status"))
    rows = [("Fine-tune status", shown, "s9_5"), ("Base model id", p.get("base_model_id"), "s9_5"),
            ("Trained model id", p.get("model_id"), "s9_5")]
    return stage_block(r, "s9_5", ev(r, rows) + (adaption(r, ft) if status == "evidence_attached" else ""))


def animal(r: Run) -> str:
    p = r.payload("s10")
    d = dd(p.get("design"))
    if not d:
        return no_content(r, "s10", "no study design in this run.")
    pa, sp = dd(d.get("power_analysis")), dd(d.get("species_selection"))
    fr = dd(p.get("falsification_rows"))
    states = H(f"{fmt(fr.get('total'))} rows: " + str(counts_text(fr.get("by_status")))) if fr else None
    rows = [("Species", sp.get("species"), "s10"), ("Strain", H(f"{fmt(sp.get('strain'))} ({fmt(sp.get('strain_status'))})"), "s10"),
            ("Adjuvant", dig(d, "adjuvant", "name"), "s10"), ("Route", dig(d, "route", "route"), "s10"),
            ("Primary endpoint", dig(d, "primary_endpoint", "name"), "s10"), ("Arms", len(rows_of(d.get("arms"))) or None, "s10"),
            ("Animals per group (allocated)", pa.get("n_per_group_allocated"), "s10"), ("Total animals", pa.get("total_animals"), "s10"),
            ("Assumed effect size d", H(f"{fmt(pa.get('effect_size_d'))} ({fmt(pa.get('effect_size_status'))})"), "s10"),
            ("Family-wise alpha, multiplicity method", H(f"{fmt(pa.get('alpha_familywise'))}, {fmt(pa.get('multiplicity_method'))}"), "s10"),
            ("Target power, achieved power", H(f"{fmt(pa.get('target_power'))}, {fmt(pa.get('achieved_power'))}"), "s10"),
            ("Challenge model", dig(d, "challenge_model", "status"), "s10"), ("Positive control", dig(d, "positive_control", "status"), "s10"),
            ("Falsification rows and their states", states, "s10"), ("Human inputs still outstanding", p.get("outstanding_human_inputs"), "s10")]
    out = [ev(r, rows)]
    arms = rows_of(d.get("arms"))
    if arms:
        out.append("<h3>Arms</h3>" + tbl(("Arm", "Role", "Dose (ug)", "Animals per group", "Primary comparison", "Source"),
                                          [(e(a.get("arm_id")), fmt(a.get("role")), fmt(a.get("dose_ug") if a.get("dose_ug") is not None or
                                                                           str(a.get("role")).startswith("candidate") else NA),
                                            fmt(a.get("n_per_group")),
                                            fmt(a.get("primary_comparison")), f'<span class="src">{e(r.src("s10"))}</span>') for a in arms]))
    tests = [f for f in rows_of(d.get("falsification_map")) if f.get("falsified_if")]
    if tests:
        out.append("<h3>Falsification rows that can be tested</h3>" + tbl(
            ("Candidate", "Upstream stage", "Animal readout", "Falsified if", "State"),
            [(e(f.get("candidate_id")), fmt(f.get("upstream_stage")), fmt(f.get("animal_readout")), fmt(f.get("falsified_if")),
              fmt(f.get("status"))) for f in tests]))
    return "".join(out)


def gate(r: Run) -> str:
    p = r.payload("s12")
    if not p:
        return ""
    rec, order = dd(p.get("provider_record")), dd(p.get("order"))
    sources = {"s8": "the order list S8 wrote", "user": "an order list supplied by a person"}
    order_text = (H(f"{fmt(order.get('status'))}, {fmt(order.get('n_sequences'))} sequence(s); source: "
                    f"{fmt(sources.get(str(order.get('source'))) or order.get('source'))}") if order.get("n_sequences") is not None
                  else order.get("status"))
    rec_text = (H(f"{fmt(rec.get('status'))}: {fmt(rec.get('provider'))} {fmt(rec.get('tool'))} {fmt(rec.get('tool_version'))}, covers every "
                  f"ordered sequence: {fmt(rec.get('covers_every_ordered_sequence'))}; {fmt(rec.get('authenticity'))}")
                if rec.get("status") == "supplied" else rec.get("status"))
    rows = [("Gate state", p.get("state"), "s12"), ("Reasons", listed(p.get("reasons")), "s12"),
            ("Order list", order_text, "s12"), ("Provider screening record", rec_text, "s12")]
    out = [ev(r, rows)]
    review = [x for x in p.get("review", []) if x not in (None, "")] if isinstance(p.get("review"), list) else []
    if review or p.get("review_required"):
        out.append(f'<div class="review"><h3><span class="chip chip-review">for review</span> {len(review)} candidate flag(s) for a person '
                   f'to look at; nothing is blocked by these</h3>'
                   + ("<ul>" + "".join(f"<li>{fmt(x)}</li>" for x in review) + "</ul>" if review
                      else f"<p>{dash()} the artifact says review is required but lists no flag.</p>") + "</div>")
    seqs = rows_of(p.get("sequences"))
    if seqs:
        checks = ("selected_candidate", "sequence_sanity", "virulence_flag", "toxin_flag")
        out.append("<h3>Ordered sequences</h3>" + tbl(
            ("Sequence", "Length", "Provider verdict", *checks),
            [(e(s.get("id")), fmt(s.get("length")), fmt(s.get("provider_verdict")),
              *(fmt(dig(s, "local_checks", c, "result")) for c in checks)) for s in seqs]))
    out.append("<h3>Limits of this gate</h3>" + str(listed(p.get("limits"))))
    return "".join(out)


def warnings_section(r: Run) -> str:
    out = [f"<h3>{e(TITLES[s])} {chip(r.state(s))}</h3><ul>" + "".join(f"<li>{e(w)}</li>" for w in a.warnings) + "</ul>"
           for s, a in ((s, r.arts[s]) for s in STAGE_ORDER if s in r.arts) if a.warnings]
    if r.errors:
        out.append("<p>Warnings of these stages could not be read: " + e(", ".join(r.errors)) + ".</p>")
    return "".join(out) or (f"<p>{dash()} no artifacts in this run.</p>" if not r.arts and not r.errors
                            else "<p>No readable artifact in this run recorded a warning.</p>")


def limits() -> str:
    return ("<p>Fixed text from README sections 4 and 7. It is not computed from this run.</p><h3>Honest ceilings (README §4)</h3>"
            + tbl(("Claim", "Honest ceiling"), [(e(a), e(b)) for a, b in CEILINGS])
            + "<h3>Deliberately absent (README §7)</h3>" + tbl(("Not implemented", "Why"), [(e(a), e(b)) for a, b in ABSENT]))


def footer(r: Run, now: str) -> str:
    mp = r.store.manifest_path
    problems = r.store.verify(check_files=False)
    rows = [("Run id", e(r.run_id)), ("Generated (UTC)", e(now)), ("VaxForge version", fmt(r.manifest.get("vaxforge_version"))),
            ("Config digest (manifest)", fmt(r.manifest.get("config_digest"))),
            ("manifest.json sha256", e(sha256_file(mp)) if mp.is_file() else dash() + " no manifest.json")]
    rows += [(f"Artifact digest {e(s)}", e(a.digest)) for s, a in r.arts.items()]
    rows.append(("Integrity check (digests and upstream chain; sidecar files not re-hashed)",
                 "<br>".join(e(p) for p in problems) if problems else "no problems found"))
    return "<h2>Source</h2>" + tbl(("Item", "Value"), rows)


def render(run_dir, now: str | None = None) -> str:
    if not Path(run_dir).is_dir():
        raise ContractError(f"{run_dir} is not a folder")
    r = Run(run_dir)
    sections = [
        ("launcher", "Run launcher", launcher(r), "Inputs are recorded here, not validated. Check the matched taxon before trusting anything below."),
        ("overview", "Pipeline overview", overview(r), "Done means a stage wrote an artifact with status ok. It does not mean its output is right."),
        ("candidates", "Ranked candidates", ranked(r), "A rank is a hypothesis from predictors this project has not validated. It says nothing about expression, immunogenicity or protection."),
        ("detail", "Candidate detail", detail(r), "Scores marked heuristic are unvalidated summaries; the epitope and structure outputs behind them are the evidence."),
        ("validation", "Validation", validation(r), "A retrospective result counts only if the holdout actually held out (README §3)."),
        ("protocol", "Wet-lab protocol", stage_block(r, "s8", protocol(r)), "A protocol is a design. Nothing in this report is a measured assay result."),
        ("robot", "Robot code", stage_block(r, "s9", robot(r)) + stage_block(r, "s9plus", animation(r)) + finetune(r),
         "Passing simulation is not correctness (README S9)."),
        ("animal", "Animal study", stage_block(r, "s10", animal(r)), "A study design for ethics review, not results. Mouse immunogenicity does not predict human protection."),
        ("prereg", "Pre-registration and gate", prereg(r), "A timestamp from this machine's clock is evidence only once its digest is published somewhere else."),
        ("warnings", "Warnings", warnings_section(r), "Only warnings a stage wrote down appear here. A stage that did not run could not warn."),
        ("limits", "Limits and absent", limits(), ""),
    ]
    badge = FIXTURE if r.fixture else ""
    body = "".join(
        f'<section id="{i}"><header><h2>{t}</h2>{badge}</header>{b}'
        + (f'<aside class="ceiling"><h3>Honest ceiling</h3><p>{c}</p></aside>' if c else "") + "</section>"
        for i, t, b, c in sections)
    toc = "".join(f'<li><a href="#{i}">{t}</a></li>' for i, t, _, _ in sections)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="color-scheme" content="light dark"><title>VaxForge report - {e(r.run_id)}</title><style>{CSS}</style></head><body>'
            f'<header class="top"><strong>VaxForge evidence report</strong><span>run <code>{e(r.run_id)}</code></span>{badge}</header>'
            f'<main><h1>What this run did, and what to trust</h1><p>Every value shows the stage and tool versions that produced it. '
            f'A dash marks a value that was not computed.</p><nav aria-label="Contents"><ol>{toc}</ol></nav>{body}</main>'
            f'<footer>{footer(r, now or utcnow_iso())}</footer></body></html>\n')


def cmd_report(args) -> int:
    out = Path(args.out) if args.out else Path(args.run_dir) / "report.html"
    try:
        page = render(args.run_dir)         # raises for a missing folder before anything is written
        out.write_text(page, encoding="utf-8")
    except (ContractError, OSError) as exc:
        print(f"report: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {out}")
    return 0


def _add_arguments(p) -> None:
    p.add_argument("run_dir")
    p.add_argument("--out", default=None, help="file to write (default: RUN_DIR/report.html, inside the run folder)")
    p.set_defaults(func=cmd_report)


def register_cli(subparsers, common):
    """`vaxforge report RUN_DIR [--out FILE]`."""
    _add_arguments(subparsers.add_parser("report", parents=[common], help="write a self-contained HTML evidence report for a run"))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="vaxforge report", description="Write a self-contained HTML evidence report for a run")
    _add_arguments(p)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
