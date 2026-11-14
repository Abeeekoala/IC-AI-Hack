# Stream C: our five-rule check against Text2WetLab's own grader

Stream D asked for this on the team board: run the five-rule check on the Text2WetLab reference solutions and on broken variants,
compare per rule with `spec_check`, and treat a disagreement as a bug in one of the two.

- **Ours**: `vaxforge.s9_validate.simulate` + `check_physical_rules` (rules `tip_before_aspirate`, `no_overdispense`,
  `no_aspirate_from_empty_well`, `tip_dropped_at_end`, `no_cross_contamination`), fed the initial well contents the task's IR implies.
- **Theirs**: `validation/t2wl_adapter.py::grade_protocol` (stream D), which runs Text2WetLab's lint, anti-hack traps, simulation and
  `spec_check.check` in a subprocess. `rule_results` has the same five rules, in the same order.
- **Clone**: Text2WetLab commit `a50139a9917a69d78eda350f14d375ec9437bd8f` (MIT), sparse (`tasks`, `eval`, `paper2protocol`), read only.
  Opentrons 7.5.0 on Python 3.10.11 (`VAXFORGE_OT_PYTHON`); the tests run on Python 3.14.
- **Code**: `tests/wetlab/test_t2wl_agreement.py` (162 tests), data in `tests/fixtures/t2wl/mutations.json` (76 mutations).
  Text2WetLab protocols are untrusted code: they only ran through the adapter (its lint, traps, subprocess) and through
  `simulate` (our lint first, then a subprocess). Nothing was exec'd in this process and nothing was written into the clone.

## Result

| | compared | agree | disagree |
|---|---|---|---|
| reference solutions, 9 tasks x 5 rules | 45 | 45 | 0 |
| mutations both graders simulate, 68 x 5 rules | 340 | 338 | 2 |

Both graders report 0 violations on all nine references. On the 76 mutations, 8 are refused by the simulator in both graders
(counted apart), 68 are compared; the only simulating disagreement left is one family of two protocols that use an air gap
(finding 1), and it is Text2WetLab's. Before the fixes five mutations disagreed; four were bugs in our check and are fixed
(findings 1 to 3). Findings 4 to 6 are not disagreements and matter for how much the agreement proves.

The broken variants that agree were built mechanically (below). The five disagreements were not found by them but by probes
chosen after reading both implementations (air gaps, mixing a reservoir, the tolerance), so five is not a base rate.

## Method

1. **Initial contents from the IR** (`derive_initial`, tested without the clone). Follows `spec_check._deck_mapping` and `check`:
   a container is mapped only if the run loaded exactly one labware with the deck's label, in the deck's slot (the module's slot for
   "<module> on <slot>"), with the deck's load name; otherwise it has no contents (spec_check reports `deck_labware` and so do we).
   IR wells are expanded as `paper2protocol.check.expand_wells` does, volumes of one well add up, a tube sits at the deck's well, a
   reservoir is a stock and gets no entry, `null` is an ample stock (we declare 1e9 uL), any other volume is tracked exactly.
2. **One protocol, two graders.** `run_ours`: `simulate`, `derive_initial`, `check_physical_rules`. `run_theirs`: `grade_protocol`.
3. **Per rule** the comparison is: the verdicts; the counts, exactly where the adapter's detail text shows every hit (fewer than
   three, and always for `tip_dropped_at_end`), otherwise ours >= theirs; for `no_cross_contamination` the first (up to three) wells;
   for the three replay rules the first (up to three) flagged *events*, when the run has only pipetting events so that
   Text2WetLab's event numbers equal our positions (32 of 32 agree). The adapter shows no more than three hits per rule, so the
   full counts above three are not compared.
4. **Mutations are data** (`mutations.json`: id, task, rule family, rules it should break, description, edit operations on the
   reference text). Each operation must hit exactly where it says, so no mutation is a silent no-op. A test checks that every mutation
   breaks exactly the rules its record says (and that controls stay clean).
5. **Known disagreements** are `disagree` entries with a reason; the test marks each `xfail(strict=True)`, so an agreement later makes
   it fail and the entry has to go.
6. **Windows.** The adapter is POSIX only (documented in `grade_protocol`). On `os.name == "nt"`, and only then, the test module replaces
   two functions of its own loaded copy of the adapter: `_run_scrubbed` (same contract, an allow-listed environment that Windows
   Python can start in, the process tree killed on timeout) and `_task_layout` (an LF copy of a task's `/data`, used only when it hashes
   as the task ships it, because Text2WetLab's data-hash trap fails on a CRLF checkout). No verdict logic is touched and
   `validation/` is not modified. On POSIX nothing is replaced.

## Numbers

The reference solutions (aspirations are ours; the adapter does not return its own count):

| task | aspirations | wells with a volume | ample wells | ours | theirs |
|---|---|---|---|---|---|
| a1-a12-100ul | 12 | 0 | 0 | 0 | 0 |
| ampure-bead-cleanup | 2688 | 96 | 0 | 0 | 0 |
| colony-pcr-screening | 576 | 0 | 192 | 0 | 0 |
| colony-pcr-screening-hard | 576 | 0 | 192 | 0 | 0 |
| ecoli-heat-shock-transformation | 2 | 1 | 1 | 0 | 0 |
| ecoli-heat-shock-transformation-hard | 16 | 16 | 0 | 0 | 0 |
| golden-gate-assembly | 156 | 22 | 9 | 0 | 0 |
| golden-gate-assembly-hard | 156 | 22 | 9 | 0 | 0 |
| split-200ul-two-wells | 2 | 0 | 0 | 0 | 0 |

(`ecoli-heat-shock-transformation-hard` simulates at its own apiLevel 2.13 since the fix of finding 6.) Agreements / comparisons by task and rule,
the reference plus the mutations both graders simulate:

| task | tip_before_aspirate | no_overdispense | no_aspirate_from_empty_well | tip_dropped_at_end | no_cross_contamination |
|---|---|---|---|---|---|
| a1-a12-100ul | 6/6 | 6/6 | 6/6 | 6/6 | 6/6 |
| ampure-bead-cleanup | 7/7 | 7/7 | 7/7 | 7/7 | 7/7 |
| colony-pcr-screening | 9/9 | 9/9 | 9/9 | 9/9 | 9/9 |
| colony-pcr-screening-hard | 4/4 | 4/4 | 4/4 | 4/4 | 4/4 |
| ecoli-heat-shock-transformation | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 |
| ecoli-heat-shock-transformation-hard | 6/6 | 6/6 | 6/6 | 6/6 | 6/6 |
| golden-gate-assembly | 7/7 | 7/7 | 7/7 | 7/7 | 7/7 |
| golden-gate-assembly-hard | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |
| split-200ul-two-wells | 23/23 | 21/23 | 23/23 | 23/23 | 23/23 |

Mutations by rule (a comparison is one rule of one mutation both graders simulated):

| rule | comparisons | both flag | both clean | only ours flags | only theirs flags | exact counts agree | cut-off counts agree | same first events |
|---|---|---|---|---|---|---|---|---|
| tip_before_aspirate | 68 | 6 | 62 | 0 | 0 | 6/6 | 0/0 | 6/6 |
| no_overdispense | 68 | 12 | 54 | 0 | 2 | 10/10 | 2/2 | 11/11 |
| no_aspirate_from_empty_well | 68 | 17 | 51 | 0 | 0 | 13/13 | 4/4 | 15/15 |
| tip_dropped_at_end | 68 | 12 | 56 | 0 | 0 | 12/12 | 0/0 | 0/0 |
| no_cross_contamination | 68 | 14 | 54 | 0 | 0 | 4/4 | 10/10 | 0/0 |
| **all five** | 340 | 61 | 277 | 0 | 2 | 45/45 | 16/16 | 32/32 |

Mutations by family (the family is the rule the edit targets; `probe` = air-gap probes, `control` = correct protocols with an unusual
pattern, which must stay clean in both):

| family | built | compared | not simulated by either | simulated by one only | comparisons | disagreements |
|---|---|---|---|---|---|---|
| tip_before_aspirate | 12 | 6 | 6 | 0 | 30 | 0 |
| no_overdispense | 10 | 10 | 0 | 0 | 50 | 0 |
| no_aspirate_from_empty_well | 14 | 14 | 0 | 0 | 70 | 0 |
| tip_dropped_at_end | 10 | 8 | 2 | 0 | 40 | 0 |
| no_cross_contamination | 11 | 11 | 0 | 0 | 55 | 0 |
| combination | 6 | 6 | 0 | 0 | 30 | 0 |
| probe | 4 | 4 | 0 | 0 | 20 | 2 |
| control | 9 | 9 | 0 | 0 | 45 | 0 |

Mutations by task: a1-a12-100ul 6, ampure-bead-cleanup 7, colony-pcr-screening 8, colony-pcr-screening-hard 4,
ecoli-heat-shock-transformation 10, ecoli-heat-shock-transformation-hard 5, golden-gate-assembly 9, golden-gate-assembly-hard 4,
split-200ul-two-wells 23 (all nine tasks, 76 in all). The mechanical edits: `new_tip='never'` without a pick-up; `pick_up_tip()` or
`drop_tip()` removed; a manual pick-up with `new_tip='never'`; one aspirate and several dispenses; a dispense larger than the
aspirate; a source that is an unfilled well or tube, a well that runs dry, a mix larger than the well; `new_tip='once'` in place of
`'always'`, a mix in a destination followed by another well, `consolidate`; a labware in the wrong slot; 8-channel versions; and
combinations of these. Regenerate the tables with `python tests/wetlab/test_t2wl_agreement.py all --workers 6 --table`.

**Not simulated by either (8, counted apart):** six `tba-*` variants (the simulator itself refuses an aspiration with no tip:
`UnexpectedTipRemovalError ... Cannot perform PREPARE_ASPIRATE without a tip attached`) and two `td-gg-last-*` variants (the literal
removal of a `drop_tip()` that a later `pick_up_tip()` then collides with: `UnexpectedTipAttachError`). Both graders refuse the
same eight, so these agree too.

## Disagreements and findings

### 1. Air gaps (a bug in ours, fixed; and a false positive in Text2WetLab, not ours to fix)

`pipette.air_gap(n)` logs an `Air gap` command with an aspirate of n uL as its sub-step. Text2WetLab's `runlog.py` sets that aspirate
aside (kind `air`) and `spec_check.replay` never adds it to the volume in the tip. Our runner passed it on as an ordinary aspirate.

- **Ours, before the fix**: the air was taken from the well it hovers over, and the tip counted as having visited that well.
  `ag-ecoli-air-gap-over-a-nearly-empty-tube` (tube drained to 5 uL, aspirate(10), `air_gap(8)`, dispense(10)): ours
  `no_aspirate_from_empty_well` 1, theirs 0. `ag-split-air-gap-above-a-destination` (dispense into A3, `air_gap(10)` above it,
  draw from the reservoir, dispense into A4): ours `no_cross_contamination` 2, theirs 0. Ours was wrong in both (nothing left the
  well; nothing from A3 entered the tip). `final_volumes` and `reference_diff` had the same error.
  **Fix** (`s9_validate.leaf_events`, `check_physical_rules`): an aspirate that is the sub-step of an `Air gap` command comes back as
  kind `air_gap`; it adds to the volume in the tip, takes nothing from any well, is not an aspiration and does not touch the
  cross-contamination state. Tests in `tests/wetlab/test_s9_validate.py`: three fail on the old code
  (`test_an_air_gap_is_air_not_drawn_from_the_well_and_it_can_be_dispensed`, `..._above_a_destination_does_not_make_the_tip_carry_that_well`,
  `test_leaf_events_marks_the_air_gap_aspirate_on_a_copy_and_final_volumes_ignore_it`); a fourth guards that a user comment reading
  "Air gap" is not taken for one.
- **Theirs, still different**: smallest protocol, `ag-split-aspirate-air-gap-dispense-all` (the reference of
  `split-200ul-two-wells` with its `transfer` replaced by these five statements):
  `p300.pick_up_tip(); p300.aspirate(100, lw_reservoir['A1']); p300.air_gap(20); p300.dispense(120, lw_plate['A1']); p300.drop_tip()`.
  Text2WetLab `no_overdispense` fails with 1 hit (the tip held 100 in its count, 120 were dispensed); ours 0. The same for
  `transfer(..., air_gap=20)` (`ag-split-transfer-air-gap-20`: two dispenses of 120, theirs 2 hits, ours 0), which is what
  Opentrons' own `transfer` dispenses after an air gap. **Who is right**: the pipette holds 100 uL of liquid and 20 of air, 120 uL, and the
  task text (its `instruction.md`) says "dispense more than it holds". The replay's `held` leaves the air out, so its definition
  contradicts its own stated rule. Text2WetLab's RNA checker handles the same case on purpose ("Air gaps are not logged as
  aspirations, so only credit liquid actually held", and clamps). We keep the air in `held`. Both entries are strict xfails.

### 2. Dispensing into a reservoir made it a tracked well (a bug in ours, fixed)

`check_physical_rules` documents "reservoir wells are ample stocks unless declared in `initial`" but tested `key in wells`, and a
dispense adds the well to `wells`. So after mixing a reservoir before drawing from it, the reservoir counted as holding only what the
mix put back. Found by the control `ctl-split-mix-in-source-before-drawing`: `p300.pick_up_tip(); p300.mix(3, 50, lw_reservoir['A1']);
p300.transfer(100.0, lw_reservoir['A1'], [lw_plate['A1'], lw_plate['B1']], new_tip='never'); p300.drop_tip()`: ours
`no_aspirate_from_empty_well` 2, theirs 0. Theirs is right (a reservoir is a stock; spec_check never tracks it). Fix: the tracked
stock wells are `frozenset(initial)`. Test: `test_dispensing_into_an_undeclared_reservoir_does_not_start_tracking_it`.

### 3. Tolerance (ours, aligned)

spec_check judges `vol > held + 1e-9` and `well < vol - 1e-9`; ours used 1e-6. `od-split-dispense-5e-7-ul-too-much`
(aspirate(100), dispense(100.0000005)): ours 0, theirs 1. It is 0.5 pL, with no physical meaning, but the module promises the
benchmark's semantics, so both tolerances are now `_VOL_TOL = 1e-9`. Test: `test_the_volume_rules_have_spec_checks_tolerance_of_1e_9_ul`.

### 4. Both graders keep one tip flag for the whole robot (shared; they agree and are both wrong)

An OT-2 has a tip per pipette. Both replays have one flag. `p300.pick_up_tip(); p20.pick_up_tip(); p20.drop_tip();` clears it while
p300 still holds its tip: the next two p300 events are called `tip_before_aspirate` by both (six `tba-*-tip-held-by-other-pipette`
variants, 2 hits each, same events), although the simulator accepts the run and each pipette has its tip. The same flag hides a
tip left on one pipette (`td-split-tip-masked-by-other-pipette`: p300 ends holding a tip, neither flags `tip_dropped_at_end`).
These seven are recorded with `both_wrong` and the test asserts the agreement and that it differs from the physical expectation.
Not fixed in ours: it is not a disagreement, and a per-pipette flag would make our numbers differ from the benchmark's on
two-pipette protocols. Reported here, for the decision of whoever owns the rule semantics.

### 5. `tip_before_aspirate` cannot fire on a protocol that simulates, except through finding 4

Opentrons refuses an aspiration or a mix without a tip while simulating, so such a protocol never reaches the
replay: all six `tba-*` variants that remove a pick-up (`new_tip='never'`, or the `pick_up_tip()` of a mix deleted) are refused by both. The agreement on this rule over simulated protocols
(68 comparisons, 6 of them flagged) is therefore weak evidence: the six flagged ones are the artefact of finding 4.

### 6. Our simulator cannot run a module protocol below apiLevel 2.14 (a bug in `_ot_sim_runner.py`, fixed 2026-11-14)

`_deck_snapshot` reads `getattr(module, 'model', '')`; `ModuleContext.model` raises `APIVersionError` below 2.14 and `getattr`'s
default does not catch it. `simulate()` then returns `ok: false` for a three-line protocol (`metadata = {'apiLevel': '2.13'}`,
`protocol.load_module('thermocycler')`), which Opentrons and Text2WetLab's grader run. `ecoli-heat-shock-transformation-hard`'s
reference is one. Not a rule bug. Fixed on 2026-11-14: `_ot_sim_runner._module_model` treats the
`APIVersionError` as an absent model (`""` below 2.14), the strict xfail
(`test_our_simulator_runs_a_protocol_that_loads_a_module_below_api_2_14`) is now an ordinary passing test, and the workaround
in `run_ours` (re-simulating a copy with apiLevel 2.14) was removed (`ecoli-heat-shock-transformation-hard` runs at its own apiLevel 2.13).

## Not covered

- **The two RNA tasks (no IR, no `spec_check`).** Not run. Ours cannot simulate the references: they load the custom labware
  `thermo_96_wellplate_200ul` and `simulate` has no custom-labware path (`FileNotFoundError [line 37]: Unable to find a labware`).
  The adapter cannot grade them on this Windows checkout: its `task_files_modified` trap hashes `/data/labware/...` under a relative
  path with a backslash and fails (`AdapterError ... /data differs from the shipped files: ['labware/thermo_96_wellplate_200ul.json',
  'labware\\thermo_96_wellplate_200ul.json']`); the adapter documents itself as POSIX only. Even where both run, the two mapped rules are
  not ours: `reservoir_columns_within_15ml` is a capacity limit and `fresh_tip_per_sample_no_cross_contact` counts one tip per sample,
  and the adapter's docstring says Text2WetLab's generic rules flag the RNA reference itself (6 aspirate_from_empty, 3 cross-contamination).
  No agreement is expected there and none was measured. The other three rules are not graded for those tasks at all.
- **`ecoli-heat-shock-transformation-hard`'s `task_files_modified` trap** is skipped by the adapter (its `/data/paper.txt` is not in
  the repo). The five rules are unaffected; the grade carries `data_files_unavailable`.
- **Counts above three hits** are compared only as ours >= theirs (and the first wells for cross-contamination); the adapter keeps the
  first three. `spec_check`'s `aspirations` count is not returned by the adapter, so ours is not compared to it.
- **Event numbers** are compared only where the run has pipetting events and nothing else Text2WetLab numbers (not
  `ecoli-heat-shock-transformation-hard`, which has thermocycler steps).
- **Not independent**: the derivation of initial contents re-implements `spec_check`'s deck mapping (agreement on
  `no_aspirate_from_empty_well` after a wrong-slot labware, `ae-pcr-colony-plate-in-the-wrong-slot`, tests that re-implementation, not
  the rule); both replays are one algorithm in two codings; and finding 4 is shared.
- Variants are small edits of nine references; nothing here says how the rules behave on protocols with modules, air-gap-heavy
  or multi-pipette workflows beyond the probes listed.

## Running it

`T2WL_CLONE` (default: `../_t2wl` next to the repo) and `VAXFORGE_OT_PYTHON` must point at the clone at the commit above and at a
Python 3.10 with opentrons 7.5.0; otherwise the clone-dependent tests are skipped with the reason. The helpers
(`expand_wells`, `derive_initial`, `apply_ops`, `compare`, the data checks) are tested without them.
`python -m pytest tests/wetlab/test_t2wl_agreement.py -q -p no:cacheprovider` runs the whole matrix in about two minutes at six
workers (`T2WL_WORKERS`). `python tests/wetlab/test_t2wl_agreement.py --help` lists the command line (`refs`, `mutations`, `all`,
`--only`, `--json`, `--table`, `rna`).

## For stream D

1. The adapter returns Text2WetLab's `no_overdispense` as failed for any protocol that dispenses an air gap together with the liquid
   (`transfer(..., air_gap=n)`, or `air_gap(n)` then `dispense(liquid + n)`). By the task's own text that is not an overdispense.
   A model that uses air gaps will be charged a `rule_violations` it did not earn; consider saying so where `winrate.py`'s rule 2
   is described, or counting it apart.
2. Both replays keep one tip flag for the robot (finding 4), and `tip_before_aspirate` is unreachable for a simulating protocol
   (finding 5): as a win-rate input that rule is close to constant.
3. The adapter is POSIX only. `_run_scrubbed` uses `PATH=/usr/bin:/bin` and `os.killpg`, and the data-hash trap fails on a CRLF
   checkout and on a backslash in a relative path, so the RNA tasks cannot be graded on Windows. Returning `aspirations` from
   `spec_check.check` would let this comparison also check that both runs saw the same pipetting.
