"""S6 maths and S7 geometry: checked against closed-form values, not against themselves."""
import b_fixtures  # adds src to sys.path
from b_fixtures import cfg_fixture, freq_file_fixture, run_dir_fixture

_FIXTURES = (cfg_fixture, freq_file_fixture, run_dir_fixture)   # pytest registers fixtures found as module attributes (names set in b_fixtures)
_ = b_fixtures
import numpy as np
import pytest

from vaxforge.prediction.popcov import (FrequencyTable, coverage_stats, export_iedb_epitope_allele_file,
                                        fairness_report, greedy_select)
from vaxforge.prediction.structure_utils import (kabsch, lddt_ca, parse_structure_ca, plddt_summary,
                                                 rmsd_after_superposition, tm_score)

from b_fakes import helix_coords, synth_seq, write_pdb_ca


@pytest.fixture
def table(freq_file):
    return FrequencyTable.from_tsv(freq_file)


# ------------------------------------------------------------------------------ coverage
def test_single_allele_matches_hardy_weinberg(table):
    # one epitope restricted by A*02:01 only: coverage = 1 - (1 - f)^2 ; World f = 0.15
    r = coverage_stats({"e1": ["HLA-A*02:01"]}, table, "World", "class_i")
    assert r.coverage == pytest.approx(1 - 0.85 ** 2)
    # IEDB counts a homozygote's hits ONCE, so with one epitope hits are 0/1 and the mean equals the coverage
    assert r.average_hits == pytest.approx(r.coverage)
    assert sum(r.hit_distribution) == pytest.approx(1.0)


def test_two_loci_are_combined_independently(table):
    r = coverage_stats({"e1": ["HLA-A*02:01"], "e2": ["HLA-B*07:02"]}, table, "Europe", "class_i")
    pa, pb = 1 - (1 - 0.25) ** 2, 1 - (1 - 0.13) ** 2
    assert r.coverage == pytest.approx(1 - (1 - pa) * (1 - pb))
    assert sum(r.hit_distribution) == pytest.approx(1.0)


def test_hits_count_epitopes_per_allele_and_homozygotes_count_once(table):
    # three epitopes share A*02:01 (Europe f=0.25): any carrier (het or hom) has 3 hits - homozygotes are NOT doubled (IEDB rule)
    epi = {f"e{i}": ["HLA-A*02:01"] for i in range(3)}
    r = coverage_stats(epi, table, "Europe", "class_i")
    assert r.hit_distribution[0] == pytest.approx(0.75 ** 2)
    assert r.hit_distribution[3] == pytest.approx(2 * 0.25 * 0.75 + 0.25 ** 2)
    assert r.average_hits == pytest.approx(3 * (1 - 0.75 ** 2))
    assert r.pc90_integer == 0
    assert 0 < r.pc90 < 1                                               # IEDB interpolates when coverage < 90%
    # data = [100, 43.75, 43.75, 43.75]; IEDB's plateau-skipping moves index2 to the end of the flat run (3):
    # pc90 = 3 - (90-43.75)/(100-43.75) * 3  (a quirk we reproduce on purpose - see the oracle test below)
    assert r.pc90 == pytest.approx(3 - (90 - 43.75) / (100 - 43.75) * 3)


def test_pc90_is_largest_n_with_90_percent_at_least_n(table):
    # cover most Europeans at several loci so >= 90% carry at least one hit
    epi = {"a": ["HLA-A*01:01", "HLA-A*02:01", "HLA-A*24:02"], "b": ["HLA-B*07:02", "HLA-B*35:01", "HLA-B*58:01"],
           "c": ["HLA-DRB1*01:01", "HLA-DRB1*07:01", "HLA-DRB1*15:01"], "d": ["HLA-DQB1*02:01", "HLA-DQB1*03:02"]}
    r = coverage_stats(epi, table, "Europe", "combined")
    assert r.coverage > 0.9 and r.pc90_integer >= 1
    tail = [sum(r.hit_distribution[n:]) for n in range(len(r.hit_distribution))]      # P(H >= n)
    n = r.pc90_integer
    assert tail[n] >= 0.9 - 1e-9 and (n + 1 >= len(tail) or tail[n + 1] < 0.9)
    assert n <= r.pc90 <= n + 1                                                       # interpolated value sits between n and n+1


def test_modes_split_by_class_and_report_unmapped(table):
    epi = {"e1": ["HLA-A*02:01", "HLA-DRB1*01:01"], "e2": ["HLA-A*68:99"]}        # A*68:99 is not in the table
    c1 = coverage_stats(epi, table, "World", "class_i")
    c2 = coverage_stats(epi, table, "World", "class_ii")
    cc = coverage_stats(epi, table, "World", "combined")
    assert c1.unmapped_alleles == ["HLA-A*68:99"] and c2.unmapped_alleles == []
    assert cc.coverage > max(c1.coverage, c2.coverage)


def test_heterodimer_falls_back_to_beta_chain_and_says_so(table):
    r = coverage_stats({"e": ["HLA-DQA1*05:01/DQB1*02:01"]}, table, "World", "class_ii")
    assert r.coverage == pytest.approx(1 - (1 - 0.12) ** 2)
    assert r.approximations == ["beta_chain_frequency_for_heterodimer"]


def test_sample_sizes_stay_unknown_unless_provided(table, tmp_path, freq_file):
    assert table.sample_sizes["Europe"] is None
    sz = tmp_path / "n.tsv"
    sz.write_text("Europe\t12345\n")
    t2 = FrequencyTable.from_tsv(freq_file, sz)
    assert t2.sample_sizes["Europe"] == 12345 and t2.sample_sizes["World"] is None


def test_frequency_table_rejects_wrong_format(tmp_path):
    bad = tmp_path / "bad.tsv"
    bad.write_text("allele\tfreq\nHLA-A*01:01\t0.1\n")
    from vaxforge.prediction.errors import ContractError
    with pytest.raises(ContractError, match="IEDB user-population format"):
        FrequencyTable.from_tsv(bad)


# --------------------------------------------------------------------------- fairness
def test_greedy_select_is_deterministic_and_optimises_the_target_population(table):
    epi = {"euro": ["HLA-A*01:01", "HLA-B*07:02"], "asia": ["HLA-A*24:02"], "afr": ["HLA-B*58:01", "HLA-B*35:01"],
           "weak": ["HLA-DRB1*01:01"]}
    s_eu = greedy_select(epi, table, "Europe", 1, "combined")
    s_as = greedy_select(epi, table, "East Asia", 1, "combined")
    assert s_eu == ["euro"] and s_as == ["asia"]
    assert greedy_select(epi, table, "Europe", 2, "combined") == greedy_select(epi, table, "Europe", 2, "combined")


def test_fairness_reports_loss_when_optimising_on_europe_only(table):
    epi = {"euro": ["HLA-A*01:01", "HLA-B*07:02"], "asia": ["HLA-A*24:02", "HLA-B*35:01"], "afr": ["HLA-B*58:01", "HLA-B*35:01"]}
    rep = fairness_report(epi, table, reference_population="Europe", populations=["Europe", "East Asia", "West Africa"], k=1)
    assert rep["available"] and rep["reference_set"] == ["euro"]
    rows = {r["population"]: r for r in rep["rows"]}
    assert rows["Europe"]["coverage_lost"] == pytest.approx(0.0)
    assert rows["East Asia"]["coverage_lost"] > 0 and rows["West Africa"]["coverage_lost"] > 0
    assert rep["mean_coverage_lost_excluding_reference"] > 0
    assert fairness_report(epi, table, reference_population="Atlantis", populations=["Europe"], k=1)["available"] is False


def test_iedb_export_is_tab_separated_without_header(tmp_path):
    p = export_iedb_epitope_allele_file({"SIINFEKL": ["HLA-A0201", "HLA-B*07:02"], "empty": []}, tmp_path / "x.tsv")
    assert p.read_text() == "SIINFEKL\tHLA-A*02:01,HLA-B*07:02\n"


# ---------------------------------------------------------------------------- geometry
def _random_rotation(seed=1):
    q, _ = np.linalg.qr(np.random.default_rng(seed).normal(size=(3, 3)))
    return q if np.linalg.det(q) > 0 else -q


def test_kabsch_recovers_rigid_motion():
    P = helix_coords(40, noise=0.5, seed=3)
    R, t = _random_rotation(), np.array([5.0, -3.0, 12.0])
    Q = P @ R.T + t
    assert rmsd_after_superposition(P, Q) == pytest.approx(0.0, abs=1e-8)


def test_tm_score_and_lddt_invariants():
    P = helix_coords(60, noise=0.3, seed=4)
    Q = P @ _random_rotation(2).T + np.array([1.0, 2.0, 3.0])
    assert tm_score(Q, P) == pytest.approx(1.0, abs=1e-6)
    assert lddt_ca(Q, P) == pytest.approx(1.0, abs=1e-9)                 # superposition-free
    scrambled = np.random.default_rng(9).normal(0, 12, P.shape)
    assert tm_score(scrambled, P) < 0.35 and lddt_ca(scrambled, P) < 0.5
    noisy = P + np.random.default_rng(5).normal(0, 1.0, P.shape)
    assert 0.5 < tm_score(noisy, P) < 1.0 and 0.5 < lddt_ca(noisy, P) < 1.0


def test_tm_score_search_beats_global_superposition_for_a_hinge():
    P = helix_coords(80)
    Q = P.copy()
    Q[40:] = (Q[40:] - Q[40]) @ _random_rotation(7).T + Q[40]            # rotate the second domain about a hinge
    glob = 1.0 / (1.0 + (np.linalg.norm(Q @ kabsch(Q, P)[0].T + kabsch(Q, P)[1] - P, axis=1) / (1.24 * (80 - 15) ** (1 / 3) - 1.8)) ** 2)
    assert tm_score(Q, P) >= glob.mean() - 1e-9


def test_parse_pdb_and_cif_ca_and_plddt_scale(tmp_path):
    seq = synth_seq(1, 30)
    xyz = helix_coords(30)
    pdb = write_pdb_ca(tmp_path / "m.pdb", seq, xyz, 0.85)               # 0-1 scale B-factors
    ca = parse_structure_ca(pdb)
    assert ca.sequence == seq and np.allclose(ca.coords, xyz, atol=1e-3)
    s = plddt_summary(ca.bfactors)
    assert s["mean_plddt"] == pytest.approx(85.0) and s["scale_detected"].startswith("0-1")
    cif = tmp_path / "m.cif"
    inv = {"A": "ALA", "C": "CYS", "D": "ASP", "E": "GLU", "F": "PHE", "G": "GLY", "H": "HIS", "I": "ILE", "K": "LYS", "L": "LEU",
           "M": "MET", "N": "ASN", "P": "PRO", "Q": "GLN", "R": "ARG", "S": "SER", "T": "THR", "V": "VAL", "W": "TRP", "Y": "TYR"}
    body = "\n".join(f"ATOM {i + 1} C CA . {inv[a]} A 1 {i + 1} ? {x:.3f} {y:.3f} {z:.3f} 1.0 {92.0:.2f} {i + 1} {inv[a]} A CA 1"
                     for i, (a, (x, y, z)) in enumerate(zip(seq, xyz)))
    cif.write_text("data_x\nloop_\n_atom_site.group_PDB\n_atom_site.id\n_atom_site.type_symbol\n_atom_site.label_atom_id\n"
                   "_atom_site.label_alt_id\n_atom_site.label_comp_id\n_atom_site.label_asym_id\n_atom_site.label_entity_id\n"
                   "_atom_site.label_seq_id\n_atom_site.pdbx_PDB_ins_code\n_atom_site.Cartn_x\n_atom_site.Cartn_y\n_atom_site.Cartn_z\n"
                   "_atom_site.occupancy\n_atom_site.B_iso_or_equiv\n_atom_site.auth_seq_id\n_atom_site.auth_comp_id\n"
                   "_atom_site.auth_asym_id\n_atom_site.auth_atom_id\n_atom_site.pdbx_PDB_model_num\n" + body + "\n#\n")
    cc = parse_structure_ca(cif)
    assert cc.sequence == seq and plddt_summary(cc.bfactors)["scale_detected"] == "0-100"


# ---------------------------------------------------------------- oracle: port of the IEDB algorithm
import random                                                                          # noqa: E402

import b_iedb_reference as iedb_reference                                                                  # noqa: E402


def _random_scenario(seed):
    rng = random.Random(seed)
    loci = {}
    for locus, n_alleles in (("HLA-A", 6), ("HLA-B", 7), ("HLA-DRB1", 5)):
        raw = [rng.random() for _ in range(n_alleles)]
        total = rng.choice([0.7, 0.93, 1.0, 1.04])                                     # <1 -> UNKNOWN mass, >1 -> renormalised
        loci[locus] = [(f"{locus}*{i + 1:02d}:01", total * r / sum(raw)) for i, r in enumerate(raw)]
    alleles = [a for v in loci.values() for a, _ in v]
    epitopes = [rng.sample(alleles, rng.randint(1, 5)) for _ in range(rng.randint(1, 9))]
    return loci, epitopes


@pytest.mark.parametrize("seed", range(25))
def test_matches_port_of_the_official_iedb_algorithm(seed, tmp_path):
    loci, epitopes = _random_scenario(seed)
    lines = ["MHC class\tMHC locus\tMHC allele\tPop"]
    for locus, lst in loci.items():
        lines += [f"{'II' if 'DRB' in locus else 'I'}\t{locus}\t{a}\t{f}" for a, f in lst]
    f = tmp_path / "t.tsv"
    f.write_text("\n".join(lines) + "\n")
    table = FrequencyTable.from_tsv(f)
    mine = coverage_stats({f"e{i}": e for i, e in enumerate(epitopes)}, table, "Pop", "combined")
    cov, avg, pc90 = iedb_reference.coverage(loci, epitopes)
    assert mine.coverage == pytest.approx(cov, abs=1e-9)
    assert mine.average_hits == pytest.approx(avg, abs=1e-9)
    if pc90 is not None:                                                             # official code divides by zero on a flat curve
        assert mine.pc90 == pytest.approx(pc90, abs=1e-9)


# ---- found on real IEDB frequency data (2026-11-14) ---------------------------------------------
@pytest.mark.parametrize("seed", range(10))
def test_fairness_loss_is_never_negative_and_greedy_matches_the_scorer(seed, tmp_path):
    loci, epitopes = _random_scenario(seed)
    lines = ["MHC class\tMHC locus\tMHC allele\tRef\tOther"]
    rng = random.Random(seed)
    for locus, lst in loci.items():
        lines += [f"{'II' if 'DRB' in locus else 'I'}\t{locus}\t{a}\t{f}\t{rng.random() * f * 2}" for a, f in lst]
    f = tmp_path / "t.tsv"
    f.write_text("\n".join(lines) + "\n")
    table = FrequencyTable.from_tsv(f)
    epi = {f"e{i}": e for i, e in enumerate(epitopes)}
    for k in (1, 2, 3):
        rep = fairness_report(epi, table, reference_population="Ref", populations=["Ref", "Other"], k=k, mode="combined")
        assert all(r["coverage_lost"] >= -1e-12 for r in rep["rows"])
        chosen = greedy_select(epi, table, "Other", k, "combined")
        assert len(chosen) <= k


@pytest.mark.parametrize("seed", range(30))
def test_greedy_k1_is_optimal_under_the_scorer_even_when_frequencies_exceed_one(seed, tmp_path):
    loci, epitopes = _random_scenario(seed)                                   # totals include 1.04 -> renormalised by the scorer
    lines = ["MHC class\tMHC locus\tMHC allele\tP"]
    for locus, lst in loci.items():
        lines += [f"{'II' if 'DRB' in locus else 'I'}\t{locus}\t{a}\t{f}" for a, f in lst]
    f = tmp_path / "t.tsv"
    f.write_text("\n".join(lines) + "\n")
    table = FrequencyTable.from_tsv(f)
    epi = {f"e{i}": e for i, e in enumerate(epitopes)}
    best = max(coverage_stats({k: v}, table, "P", "combined").coverage for k, v in epi.items())
    pick = greedy_select(epi, table, "P", 1, "combined")
    assert coverage_stats({k: epi[k] for k in pick}, table, "P", "combined").coverage == pytest.approx(best, abs=1e-12)


def test_frequency_mass_reveals_sparse_source_data(tmp_path):
    f = tmp_path / "t.tsv"
    f.write_text("MHC class\tMHC locus\tMHC allele\tFull\tSparse\nI\tHLA-A\tHLA-A*01:01\t0.6\t0.02\nI\tHLA-A\tHLA-A*02:01\t0.4\t0.03\n")
    table = FrequencyTable.from_tsv(f)
    epi = {"e": ["HLA-A*01:01", "HLA-A*02:01"]}
    assert coverage_stats(epi, table, "Full", "class_i").min_locus_frequency_mass == pytest.approx(1.0)
    r = coverage_stats(epi, table, "Sparse", "class_i")
    assert r.min_locus_frequency_mass == pytest.approx(0.05) and r.coverage < 0.1          # coverage limited by missing data, not the set
