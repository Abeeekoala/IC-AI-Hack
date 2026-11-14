"""Line-by-line port of the official IEDB Population Coverage algorithm (v3.0.2, population_calculation.py)
used as an independent oracle for ``vaxforge.prediction.popcov``. Only the arithmetic is ported; I/O is replaced
by plain Python structures.
"""
from collections import OrderedDict


def adjusted_genotype(locus_data):                       # get_adjusted_genotype
    out = OrderedDict()
    for locus, allele_genotype in locus_data.items():
        alleles = [a for a, _ in allele_genotype]
        genos = [g for _, g in allele_genotype]
        total = sum(genos)
        if total > 1:
            genos = [g / sum(genos) for g in genos]
        if total < 1:
            alleles.append("UNKNOWN")
            genos.append(1 - sum(genos))
        out[locus] = list(zip(alleles, genos))
    return out


def count_hits(locus_map, epitope_alleles):              # count_hits
    total_hits = OrderedDict()
    for locus, ag_map in locus_map.items():
        for allele in sorted(ag_map):
            total_hits[allele] = sum(1 for alleles in epitope_alleles if tuple(alleles).count(allele))
    return total_hits


def compute_frequency(locus_map, total_hits):            # compute_frequency
    out = []
    for locus, ag_map in locus_map.items():
        allele_list = sorted(ag_map)
        lf = {}
        for i in range(len(allele_list)):
            hit1, g1 = total_hits[allele_list[i]], ag_map[allele_list[i]]
            for j in range(len(allele_list)):
                hit2, g2 = total_hits[allele_list[j]], ag_map[allele_list[j]]
                total_hit = hit1
                if i != j:
                    total_hit = hit1 + hit2
                lf[total_hit] = lf.get(total_hit, 0) + g1 * g2
        out.append(lf)
    return out


def merge_loci(a, b):                                    # merge_loci
    merged = {}
    for h1, g1 in a.items():
        for h2, g2 in b.items():
            merged[h1 + h2] = merged.get(h1 + h2, 0) + g1 * g2
    return merged


def calculate_pc90(data):                                # calculate_pc90
    data = data[::-1]
    index1 = 0
    index2 = len(data) - 1
    for i in range(len(data) - 2, -1, -1):
        if data[i] == 90:
            pass
        if data[i] > 90:
            index1 = i
            break
    index2 = index1 + 1
    if index2 > len(data) - 1:
        index2 = len(data) - 1
    cur_value = data[index2]
    for i in range(index2, len(data)):
        if data[i] == cur_value:
            index2 = i
        if data[i] < cur_value:
            break
    _range = data[index1] - data[index2]
    _fraction = (90 - data[index2]) / _range
    return index2 - (_fraction * (index2 - index1))


def coverage(locus_data, epitope_alleles):
    """locus_data: {locus: [(allele, freq), ...]}; epitope_alleles: [[allele, ...], ...] -> (coverage, average_hit, pc90)."""
    locus_map = OrderedDict((l, OrderedDict(v)) for l, v in adjusted_genotype(locus_data).items())
    frequency = compute_frequency(locus_map, count_hits(locus_map, epitope_alleles))
    frequency = [dict(sorted(d.items())) for d in frequency]      # get_frequency sorts each locus dict (matters for `last`)
    result = frequency[0]
    for f in frequency[1:]:
        result = merge_loci(result, f)
    last = list(result.keys())[-1] if result else 0
    xs = list(range(last + 1))
    ys = [result.get(i, 0.0) for i in xs]
    pct = {k: v * 100 for k, v in zip(xs, ys)}
    cum = []
    for k, v in sorted(pct.items(), reverse=True):
        cum.append(v if not cum else v + cum[-1])
    cov = sum(ys[i] for i in xs if xs[i] != 0)
    n = sum(ys)
    avg = sum(x * y for x, y in zip(xs, ys)) / n if n else 0.0
    try:
        pc90 = calculate_pc90(cum)
    except ZeroDivisionError:                       # the official code crashes here (flat cumulative curve)
        pc90 = None
    return cov, avg, pc90
