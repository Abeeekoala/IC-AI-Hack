"""S9.5: seeded synthetic training pairs for the S9 OT-2 code generator (README S9.5).

The licensed public protocols clear only a few dozen usable pairs, far below Adaption's documented minimum of 1,000
effective SFT rows. This module writes the rest: each row is a task statement (labware with labels, pipettes, starting
liquids, ordered steps) and an OT-2 Python protocol that does exactly those steps. Both are rendered from one list of
operations, so the prompt and the code cannot disagree.

What it guarantees, and what it does not:
  * Deterministic: the same seed and index give the same row.
  * No held-out procedure by construction and by check: the vocabulary avoids the held-out families (no PCR, primers,
    magnetic modules, thermocyclers, a 42 C step, plain one-volume row fills) and ``accept`` runs the same family
    labeller as the licensed corpus, keeping only rows it labels ``none`` without doubt.
  * Nothing here runs a protocol. ``simulate_pass`` is left null: the caller simulates each row with
    opentrons_simulate 7.5.0 (s9_5_corpus.simulate_candidates), exactly as for the licensed rows, and a row that fails
    simulation is excluded by the manifest like any other.
  * The rows are our own, written from templates. They teach the API and the prompt-to-code mapping; they carry less
    variety than protocols written by people, and the manifest records them as origin "synthetic" so a result is never
    reported as if it came from licensed data alone.
Standard library only (plus s9_5_corpus, same stream).
"""
from __future__ import annotations

import random
import re

SOURCE = "vaxforge-synthetic"
APIS = ("2.13", "2.14", "2.15")
PIPETTES = {  # name: (min uL, max uL, tip rack)
    "p20_single_gen2": (1, 20, "opentrons_96_tiprack_20ul"),
    "p300_single_gen2": (20, 300, "opentrons_96_tiprack_300ul"),
    "p1000_single_gen2": (100, 1000, "opentrons_96_tiprack_1000ul"),
}
PLATES = ("corning_96_wellplate_360ul_flat", "nest_96_wellplate_200ul_flat", "biorad_96_wellplate_200ul_pcr",
          "nest_96_wellplate_100ul_pcr_full_skirt")
RESERVOIRS = ("nest_12_reservoir_15ml", "usascientific_12_reservoir_22ml")
TUBE_RACKS = ("opentrons_24_tuberack_nest_1.5ml_snapcap", "opentrons_24_tuberack_nest_2ml_snapcap")
LIQUIDS = ("dye solution", "assay buffer", "sample diluent", "growth medium", "inducer stock", "standard stock",
           "substrate solution", "stop solution", "wash buffer", "reagent A", "reagent B", "carrier solution")
ROWS = "ABCDEFGH"


def _wells(rows, cols):
    return [f"{r}{c}" for c in cols for r in rows]


def _span(wells):
    """'A1 to A6' for a contiguous row or column run, else a comma list."""
    if len(wells) == 1:
        return wells[0]
    rs, cs = [w[0] for w in wells], [int(w[1:]) for w in wells]
    if len(set(rs)) == 1 and cs == list(range(cs[0], cs[0] + len(cs))):
        return f"{wells[0]} to {wells[-1]}"
    if len(set(cs)) == 1 and [ROWS.index(r) for r in rs] == list(range(ROWS.index(rs[0]), ROWS.index(rs[0]) + len(rs))):
        return f"{wells[0]} to {wells[-1]}"
    return ", ".join(wells)


def _ident(label):
    return re.sub(r"\W+", "_", label).strip("_").lower()


def _pipette_for(vol):
    for name in ("p20_single_gen2", "p300_single_gen2", "p1000_single_gen2"):
        lo, hi, _ = PIPETTES[name]
        if lo <= vol <= hi:
            return name
    raise ValueError(f"no pipette for {vol} uL")


class Protocol:
    """Deck, liquids and an ordered list of operations; renders the prompt and the code."""

    def __init__(self, rng, title, goal):
        self.rng, self.title, self.goal = rng, title, goal
        self.api = rng.choice(APIS)
        self.deck = []      # (slot, load name, label, on_module or None)
        self.module = None  # (kind, slot, label)
        self.liquids = []   # (label, well, name, volume)
        self.ops = []
        self.style = rng.choice(("api", "loop"))

    def labware(self, load_name, label, slot=None):
        slot = slot or next(s for s in (1, 2, 3, 4, 5, 6, 7, 8, 9) if s not in {d[0] for d in self.deck} | {self.module[1] if self.module else 0})
        self.deck.append((slot, load_name, label))
        return label

    def liquid(self, label, well, name, volume):
        self.liquids.append((label, well, name, volume))

    def transfer(self, vol, src, src_wells, dst, dst_wells, liquid, tip="always", mix=None):
        if mix:  # the mix volume must fit the pipette that makes the transfer
            mix = (mix[0], min(mix[1], PIPETTES[_pipette_for(vol)][1]))
        self.ops.append(("transfer", vol, src, list(src_wells), dst, list(dst_wells), liquid, tip, mix))

    def mix(self, label, wells, reps, vol):
        self.ops.append(("mix", label, list(wells), reps, vol))

    def delay(self, minutes):
        self.ops.append(("delay", minutes))

    def temp(self, celsius):
        self.ops.append(("temp", celsius))

    def pipettes(self):
        names = []
        for op in self.ops:
            if op[0] == "transfer":
                n = _pipette_for(op[1])
                if n not in names:
                    names.append(n)
            elif op[0] == "mix":
                n = _pipette_for(op[4])
                if n not in names:
                    names.append(n)
        return names[:2]

    # ------------------------------------------------------------------------------------------- prompt
    def prompt(self):
        pips = self.pipettes()
        mounts = dict(zip(pips, self.rng_mounts))
        lines = [f"Write an Opentrons OT-2 Python protocol using Python Protocol API version {self.api}.", "",
                 f"Task: {self.goal}", f"Protocol name: {self.title}", "",
                 "Deck (load each labware with exactly the label shown):"]
        for slot, load, label in sorted(self.deck):
            lines.append(f"- slot {slot}: {load}, label \"{label}\"")
        if self.module:
            lines.append(f"- slot {self.module[1]}: {self.module[0]} module, with {self.module[3]} on it, label \"{self.module[2]}\"")
        lines += ["", "Pipettes:"]
        for i, name in enumerate(pips):
            lines.append(f"- {name} on the {mounts[name]} mount, tip rack {PIPETTES[name][2]} in slot {10 + i}")
        lines += ["", "Liquids at the start:"]
        lines += [f"- {label}, well {well}: {name}, {vol} uL" for label, well, name, vol in self.liquids]
        lines += ["", "Steps, in order:"]
        for i, op in enumerate(self.ops, 1):
            lines.append(f"{i}. {self._step_text(op)}")
        return "\n".join(lines)

    def _step_text(self, op):
        if op[0] == "transfer":
            _, vol, src, sw, dst, dw, liquid, tip, mix = op
            tipw = {"always": "a new tip for each transfer", "once": "one tip for the whole step"}[tip]
            text = f"Transfer {vol} uL of {liquid} from {src} well {_span(sw)} to {dst} well {_span(dw)} using {tipw}"
            if mix:
                text += f"; after each dispense, mix {mix[0]} times with {mix[1]} uL"
            return text + "."
        if op[0] == "mix":
            return f"Mix {op[1]} well {_span(op[2])}, {op[3]} times with {op[4]} uL, a new tip for each well."
        if op[0] == "delay":
            return f"Wait {op[1]} minutes."
        return f"Set the temperature module to {op[1]} C and wait until it is reached."

    # -------------------------------------------------------------------------------------------- code
    def code(self):
        pips = self.pipettes()
        mounts = dict(zip(pips, self.rng_mounts))
        out = ["from opentrons import protocol_api", "", "metadata = {", f'    "protocolName": "{self.title}",',
               f'    "apiLevel": "{self.api}",', "}", "", "", "def run(protocol: protocol_api.ProtocolContext):"]
        slots_used = 0
        if self.module:
            kind, slot, label, load = self.module
            out.append(f'    {_ident(label)}_module = protocol.load_module("temperature module gen2", {slot})')
            out.append(f'    {_ident(label)} = {_ident(label)}_module.load_labware("{load}", label="{label}")')
        for slot, load, label in sorted(self.deck):
            out.append(f'    {_ident(label)} = protocol.load_labware("{load}", {slot}, label="{label}")')
        for i, name in enumerate(pips):
            out.append(f'    tips_{i} = protocol.load_labware("{PIPETTES[name][2]}", {10 + i})')
        for i, name in enumerate(pips):
            out.append(f'    {name.split("_")[0]} = protocol.load_instrument("{name}", "{mounts[name]}", tip_racks=[tips_{i}])')
        out.append("")
        for op in self.ops:
            out += self._step_code(op)
        return "\n".join(out) + "\n"

    def _step_code(self, op):
        if op[0] == "delay":
            return [f"    protocol.delay(minutes={op[1]})"]
        if op[0] == "temp":
            label = self.module[2]
            return [f"    {_ident(label)}_module.set_temperature({op[1]})"]
        if op[0] == "mix":
            _, label, wells, reps, vol = op
            p = _pipette_for(vol).split("_")[0]
            return [f'    for well in {wells!r}:', f"        {p}.pick_up_tip()", f"        {p}.mix({reps}, {vol}, {_ident(label)}[well])",
                    f"        {p}.drop_tip()"]
        _, vol, src, sw, dst, dw, liquid, tip, mix = op
        p, s, d = _pipette_for(vol).split("_")[0], _ident(src), _ident(dst)
        if self.style == "api":
            args = f'new_tip="{tip}"' + (f", mix_after=({mix[0]}, {mix[1]})" if mix else "")
            src_arg = f'{s}["{sw[0]}"]' if len(sw) == 1 else f"[{s}[w] for w in {sw!r}]"
            return [f"    {p}.transfer({vol}, {src_arg}, [{d}[w] for w in {dw!r}], {args})"]
        pairs = [(sw[0] if len(sw) == 1 else sw[i], dw[i]) for i in range(len(dw))]
        if len(pairs) == 1:  # straight-line code for a single move
            (sw1, dw1), = pairs
            lines = [f"    {p}.pick_up_tip()", f'    {p}.aspirate({vol}, {s}["{sw1}"])', f'    {p}.dispense({vol}, {d}["{dw1}"])']
            if mix:
                lines.append(f'    {p}.mix({mix[0]}, {mix[1]}, {d}["{dw1}"])')
            return lines + [f"    {p}.drop_tip()"]
        lines = [f"    for source_well, dest_well in {pairs!r}:"]
        if tip == "always":
            lines.append(f"        {p}.pick_up_tip()")
        lines += [f"        {p}.aspirate({vol}, {s}[source_well])", f"        {p}.dispense({vol}, {d}[dest_well])"]
        if mix:
            lines.append(f"        {p}.mix({mix[0]}, {mix[1]}, {d}[dest_well])")
        if tip == "always":
            lines.append(f"        {p}.drop_tip()")
        if tip == "once":
            lines = [f"    {p}.pick_up_tip()"] + lines + [f"    {p}.drop_tip()"]
        return lines


# ------------------------------------------------------------------------------------------------ the kinds
def _plate(rng):
    return rng.choice(PLATES)


def serial_dilution(rng):
    n, row = rng.randint(5, 11), rng.choice(ROWS)
    vol = rng.choice((10, 20, 25, 40, 50))  # carried to the next well
    dil = vol * rng.choice((2, 3, 4, 9))    # diluent already in every following well
    p = Protocol(rng, "Serial dilution", f"Make a {n + 1}-point dilution series of a stock in row {row} of a plate.")
    res, plate = p.labware(rng.choice(RESERVOIRS), "diluent reservoir"), p.labware(_plate(rng), "dilution plate")
    stock, diluent = rng.choice(LIQUIDS), rng.choice(LIQUIDS)
    p.liquid(res, "A1", diluent, dil * n + 500)
    cols = list(range(2, n + 2))
    p.transfer(dil, res, ["A1"], plate, [f"{row}{c}" for c in cols], diluent, tip="once")
    p.liquid(plate, f"{row}1", stock, vol * 2)
    for c in range(1, n + 1):
        p.transfer(vol, plate, [f"{row}{c}"], plate, [f"{row}{c + 1}"], stock, tip="always", mix=(3, (vol + dil) // 2))
    return p


def column_gradient(rng):
    k = rng.randint(3, 8)
    base = rng.choice((10, 15, 20, 25, 30))
    step = rng.choice((5, 10, 15))
    vols = [base + i * step for i in range(k)]
    p = Protocol(rng, "Reagent gradient across columns", f"Fill columns 1 to {k} of a plate with increasing volumes of one reagent.")
    res, plate = p.labware(rng.choice(RESERVOIRS), "reagent reservoir"), p.labware(_plate(rng), "assay plate")
    liq = rng.choice(LIQUIDS)
    p.liquid(res, "A1", liq, sum(vols) * 8 + 1000)
    for i, v in enumerate(vols):
        p.transfer(v, res, ["A1"], plate, _wells(ROWS, [i + 1]), liq, tip="once")
    return p


def plate_copy(rng):
    rows = ROWS[: rng.randint(2, 8)]
    cols = list(range(1, rng.randint(3, 11) + 1))  # at most 88 wells: one rack of 96 tips must cover the copy and the top-up
    vol = rng.choice((25, 40, 50, 75, 100))
    p = Protocol(rng, "Copy part of a plate", f"Copy rows {rows[0]} to {rows[-1]}, columns 1 to {cols[-1]} from a source plate to a new plate.")
    a, b = p.labware(_plate(rng), "source plate"), p.labware(_plate(rng), "copy plate")
    liq = rng.choice(LIQUIDS)
    for w in _wells(rows, cols)[:3]:
        p.liquid(a, w, liq, vol * 2)
    p.transfer(vol, a, _wells(rows, cols), b, _wells(rows, cols), liq, tip="always")
    res, extra = p.labware(rng.choice(RESERVOIRS), "diluent reservoir"), rng.choice([x for x in LIQUIDS if x != liq])
    add = rng.choice([v for v in (15, 30, 60, 90) if v != vol])
    p.liquid(res, "A1", extra, add * len(rows) * len(cols) + 500)
    p.transfer(add, res, ["A1"], b, _wells(rows, cols), extra, tip="once")
    return p


def cherry_pick(rng):
    k = rng.randint(4, 14)
    src = rng.sample(_wells(ROWS, range(1, 13)), k)
    dst = rng.sample(_wells(ROWS, range(1, 13)), k)
    vols = [rng.choice((10, 15, 20, 30, 45, 60)) for _ in range(k)]
    p = Protocol(rng, "Cherry pick", f"Pick {k} selected wells from a source plate into chosen wells of a destination plate.")
    a, b = p.labware(_plate(rng), "source plate"), p.labware(_plate(rng), "destination plate")
    liq = rng.choice(LIQUIDS)
    for w in src:
        p.liquid(a, w, liq, 200)
    for i in range(k):
        p.transfer(vols[i], a, [src[i]], b, [dst[i]], liq, tip="always")
    return p


def normalise(rng):
    k = rng.randint(4, 10)
    total = rng.choice((100, 150, 200))
    samples = [rng.choice((5, 10, 15, 20, 25, 30, 40)) for _ in range(k)]
    p = Protocol(rng, "Normalise samples to one volume", f"Bring {k} samples to {total} uL each by adding diluent and then sample.")
    res, src, dst = p.labware(rng.choice(RESERVOIRS), "diluent reservoir"), p.labware(_plate(rng), "sample plate"), p.labware(_plate(rng), "normalised plate")
    diluent, sample = rng.choice(LIQUIDS), "sample"
    p.liquid(res, "A1", diluent, total * k + 1000)
    wells = _wells("A", range(1, k + 1)) if k <= 12 else []
    for i, w in enumerate(wells):
        p.liquid(src, w, sample, 100)
        p.transfer(total - samples[i], res, ["A1"], dst, [w], diluent, tip="once")
    for i, w in enumerate(wells):
        p.transfer(samples[i], src, [w], dst, [w], sample, tip="always", mix=(3, min(total - 10, 100)))
    return p


def pool_wells(rng):
    k = rng.randint(3, 10)
    vol = rng.choice((10, 20, 30, 50))
    p = Protocol(rng, "Pool wells into one tube", f"Combine {k} wells of a plate into one tube and mix.")
    plate, rack = p.labware(_plate(rng), "sample plate"), p.labware(rng.choice(TUBE_RACKS), "pool rack")
    liq = rng.choice(LIQUIDS)
    wells = _wells("B", range(1, k + 1))
    for w in wells:
        p.liquid(plate, w, liq, vol * 2)
    p.transfer(vol, plate, wells, rack, ["A1"], liq, tip="always")
    extra, add = rng.choice([x for x in LIQUIDS if x != liq]), rng.choice([v for v in (100, 200, 250) if v != vol])
    p.liquid(rack, "B1", extra, add + 300)
    p.transfer(add, rack, ["B1"], rack, ["A1"], extra, tip="always")
    p.mix(rack, ["A1"], 5, min(200, max(20, vol * k // 2)))
    return p


def incubation(rng):
    temp = rng.choice((25, 30, 33, 37, 45, 50, 55, 60))
    minutes = rng.choice((5, 10, 15, 20, 30))
    k = rng.randint(3, 8)
    vol, stop = rng.choice((20, 30, 50)), rng.choice((10, 20))
    p = Protocol(rng, "Timed incubation", f"Add reagent to {k} wells, hold them at {temp} C for {minutes} minutes, then add stop solution.")
    p.module = ("temperature", 3, "block plate", "opentrons_96_aluminumblock_nest_wellplate_100ul")
    res = p.labware(rng.choice(RESERVOIRS), "reagent reservoir")
    wells = _wells("A", range(1, k + 1))
    p.liquid(res, "A1", "substrate solution", vol * k + 500)
    p.liquid(res, "A2", "stop solution", stop * k + 500)
    p.temp(temp)
    p.transfer(vol, res, ["A1"], "block plate", wells, "substrate solution", tip="once")
    p.delay(minutes)
    p.transfer(stop, res, ["A2"], "block plate", wells, "stop solution", tip="once")
    return p


def tube_aliquots(rng):
    k = rng.randint(4, 12)
    vols = [rng.choice((100, 150, 200, 250, 300, 400, 500)) for _ in range(k)]
    p = Protocol(rng, "Aliquot from a stock tube", f"Aliquot a stock into {k} tubes, each with its own volume.")
    stock_rack, out_rack = p.labware(rng.choice(TUBE_RACKS), "stock rack"), p.labware(rng.choice(TUBE_RACKS), "aliquot rack")
    liq = rng.choice(LIQUIDS)
    p.liquid(stock_rack, "A1", liq, min(sum(vols) + 200, 1500))
    for i, v in enumerate(vols):
        p.transfer(v, stock_rack, ["A1"], out_rack, [_wells("ABCD", range(1, 7))[i]], liq, tip="once")
    return p


def matrix(rng):
    rows, cols = rng.randint(2, 5), rng.randint(2, 6)
    a_vols = [rng.choice((10, 20, 30, 40, 50)) for _ in range(rows)]
    b_vols = [rng.choice((10, 20, 30, 40)) for _ in range(cols)]
    p = Protocol(rng, "Two-reagent matrix", f"Build a {rows} by {cols} block: reagent A varies down the rows, reagent B across the columns.")
    res, plate = p.labware(rng.choice(RESERVOIRS), "reagent reservoir"), p.labware(_plate(rng), "matrix plate")
    p.liquid(res, "A1", "reagent A", 4000)
    p.liquid(res, "A2", "reagent B", 4000)
    for r in range(rows):
        p.transfer(a_vols[r], res, ["A1"], plate, [f"{ROWS[r]}{c + 1}" for c in range(cols)], "reagent A", tip="once")
    for c in range(cols):
        p.transfer(b_vols[c], res, ["A2"], plate, [f"{ROWS[r]}{c + 1}" for r in range(rows)], "reagent B", tip="once", mix=(2, 20))
    return p


def column_loop(rng):
    k = rng.randint(2, 10)
    vol = rng.choice((30, 60, 90, 120))
    add = rng.choice([v for v in (20, 45, 75) if v != vol])
    p = Protocol(rng, "Stamp between plates by column", f"Move {vol} uL from {k} source columns to the matching columns of a second plate, then top each column up with a second reagent.")
    a, b = p.labware(_plate(rng), "source plate"), p.labware(_plate(rng), "stamp plate")
    res = p.labware(rng.choice(RESERVOIRS), "reagent reservoir")
    liq = rng.choice(LIQUIDS)
    extra = rng.choice([x for x in LIQUIDS if x != liq])
    p.liquid(res, "A1", extra, add * 8 * k + 500)
    for c in range(1, k + 1):
        rr = ROWS[: rng.randint(2, 8)]
        for r in rr:
            p.liquid(a, f"{r}{c}", liq, vol * 2)
        p.transfer(vol, a, [f"{r}{c}" for r in rr], b, [f"{r}{c}" for r in rr], liq, tip="always")
        p.transfer(add, res, ["A1"], b, [f"{r}{c}" for r in rr], extra, tip="once")
    return p


KINDS = (serial_dilution, column_gradient, plate_copy, cherry_pick, normalise, pool_wells, incubation, tube_aliquots,
         matrix, column_loop)


def build(seed, index):
    """The index-th protocol of a seed, as a Protocol (deterministic)."""
    rng = random.Random(f"{seed}:{index}")
    kind = KINDS[index % len(KINDS)]
    p = kind(rng)
    p.kind = kind.__name__
    rng2 = random.Random(f"{seed}:{index}:mount")
    p.rng_mounts = rng2.sample(("left", "right"), 2)
    return p


def record(seed, index):
    """A candidate record in the corpus schema, or None when the row would break a rule (see ``accept``)."""
    from .s9_5_corpus import analyse_protocol, classify_family
    p = build(seed, index)
    code, prompt = p.code(), p.prompt()
    spec = analyse_protocol(code, f"{p.kind}.py")
    if spec is None:
        return None
    label = classify_family(spec)
    if label.family != "none" or label.doubt:
        return None
    return {"id": f"{SOURCE}:{p.kind}-{seed}-{index}", "source": SOURCE, "prompt": prompt, "code": code,
            "heldout_family": "none", "family_doubt": False, "assigned_by": "rule", "origin": "synthetic",
            "refused_by_base": None, "simulate_pass": None}


def generate(seed, count):
    """The first ``count`` accepted records of a seed, in index order, and how many indices were rejected."""
    out, rejected, i = [], 0, 0
    seen = set()
    while len(out) < count:
        if i >= count * 20:
            raise RuntimeError(f"only {len(out)} of {count} rows accepted in {i} tries: the kinds are too narrow")
        r = record(seed, i)
        i += 1
        if r is None:
            rejected += 1
            continue
        key = (r["prompt"], r["code"])
        if key in seen:
            rejected += 1
            continue
        seen.add(key)
        out.append(r)
    return out, rejected
