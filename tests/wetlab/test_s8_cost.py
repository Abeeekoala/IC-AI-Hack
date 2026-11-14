"""S8 cost: quantities from the S9 placeholder plans x prices the USER supplies (`streamc.prices`). No price is built in.

Statuses: `not_estimated` (no item priced: the quantities still appear), `partial` (some priced; no total), `estimated` (all priced).
`Protocol.cost_gbp_range` is set only for an `estimated` protocol priced in GBP. Money is added as decimals, so the arithmetic is exact.
"""

from __future__ import annotations

import copy
import json
import shutil
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from vaxforge import s8_cost, s8_protocol, s10_animal
from vaxforge.artifacts import RunStore
from vaxforge.config import Config, ConfigError
from vaxforge.s8_cost import Quantity, estimate, merge_quantities, parse_prices, protocol_quantities
from vaxforge.s8_protocol import Candidate, Protocol, build_protocol, plan_protocol, render_markdown
from vaxforge.s9_sessions import RobotParams, plan_sessions
from vaxforge.streamc_adapter import load_inputs_from_store
from vaxforge.streamc_models import CostEstimate, CostItem, CostTotal

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures" / "nmb_fixture"
CAND = Candidate(candidate_id="c1", protein_name="fHbp", rank=1, length_aa=274)
HEADING = "## Cost (placeholder quantities x your prices)"
BASIS = "test basis"


def copy_run(dest: Path) -> RunStore:
    shutil.copytree(FIXTURE, dest)
    return RunStore(dest, cache_dir=dest.parent / "cache")


def cfg(**streamc) -> Config:
    return Config(dict(streamc=streamc))


def price(amount, unit, currency="GBP") -> dict:
    return {"price": amount, "currency": currency, "unit": unit}


@pytest.fixture(scope="module")
def plans():
    return plan_sessions(build_protocol(CAND), RobotParams())


@pytest.fixture(scope="module")
def unpriced(tmp_path_factory):
    """S8 over two named proteins with an EMPTY price table: the quantities, run once and shared (read-only)."""
    st = copy_run(tmp_path_factory.mktemp("cost") / "run")
    c = cfg(proteins=["fhbp", "nada"], top_k=2)
    return st, c, s8_protocol.run(st, c)


def by_item(items) -> dict:
    return {i["item"] if isinstance(i, dict) else i.item: i for i in items}


# =========================================================================== the price table


def test_a_valid_table_is_parsed_and_normalised():
    t = parse_prices({"tip:x": price(0.1, "Tip"), "reagent:water": price(2, "mL", "gbp"), "gene_synthesis": price(50.5, " synthesis ")})
    assert t["tip:x"].price == Decimal("0.1") and t["tip:x"].unit == "tip" and t["tip:x"].currency == "GBP"
    assert t["reagent:water"].price == Decimal("2") and t["reagent:water"].unit == "ml" and t["reagent:water"].currency == "GBP"  # code upper-cased
    assert parse_prices(None) == {} and parse_prices({}) == {}
    assert parse_prices({"x": price(0, "tip")})["x"].price == Decimal(0)  # 0 is a price: "counted elsewhere"


@pytest.mark.parametrize("bad, why", [
    ([1, 2], "must be a mapping"),
    ({"tip:x": 5}, "must be a mapping with"),
    ({"tip:x": {"price": 1, "currency": "GBP"}}, "missing"),
    ({"tip:x": {"price": 1, "currency": "GBP", "unit": "tip", "per": 3}}, "unknown"),
    ({"tip:x": {"prise": 1, "currency": "GBP", "unit": "tip"}}, "needs exactly"),
    ({"tip:x": price(-1, "tip")}, "finite number >= 0"),
    ({"tip:x": price(float("nan"), "tip")}, "finite number >= 0"),
    ({"tip:x": price(float("inf"), "tip")}, "finite number >= 0"),
    ({"tip:x": price(True, "tip")}, "finite number >= 0"),
    ({"tip:x": price("1.50", "tip")}, "finite number >= 0"),
    ({"tip:x": price(1, "tip", "POUNDS")}, "3-letter code"),
    ({"tip:x": price(1, "tip", "")}, "3-letter code"),
    ({"tip:x": price(1, "  ")}, "what the price is per"),
    ({"": price(1, "tip")}, "non-empty string"),
])
def test_a_malformed_table_is_a_config_error(bad, why):
    with pytest.raises(ConfigError, match=why):
        parse_prices(bad)


def test_mixing_currencies_is_rejected_and_nothing_is_converted():
    with pytest.raises(ConfigError, match=r"mixes currencies \['GBP', 'USD'\]"):
        parse_prices({"tip:x": price(1, "tip", "GBP"), "gene_synthesis": price(50, "synthesis", "USD")})
    parse_prices({"tip:x": price(1, "tip", "USD"), "gene_synthesis": price(50, "synthesis", "usd")})  # one currency, spelt two ways: fine


# =========================================================================== quantities, from the S9 plans


def test_the_quantities_follow_the_plans_and_nothing_else(plans):
    q = by_item(protocol_quantities(plans))
    assert q["gene_synthesis"].quantity == 1 and q["gene_synthesis"].unit == "synthesis"
    assert q["robot_session"].quantity == len(plans) > 0 and q["robot_session"].unit == "session"
    racks = {"p20": ("opentrons_96_tiprack_20ul", 1), "p300": ("opentrons_96_tiprack_300ul", 1), "p300m": ("opentrons_96_tiprack_300ul", 8)}  # written out here
    tips: Counter = Counter()
    for plan in plans:
        for role, pickups in plan.tips_needed.items():
            tips[racks[role][0]] += pickups * racks[role][1]  # a multichannel pick-up is 8 tips
    assert {k: v.quantity for k, v in q.items() if k.startswith("tip:")} == {f"tip:{k}": v for k, v in tips.items()}
    assert all(v.unit == "tip" for k, v in q.items() if k.startswith("tip:")) and sum(tips.values()) > 0
    labware: Counter = Counter(lw.load_name for p in plans for lw in p.labware if lw.kind != "tiprack")
    assert {k: v.quantity for k, v in q.items() if k.startswith("labware:")} == {f"labware:{k}": v for k, v in labware.items()}
    assert not any("tiprack" in k for k in q if k.startswith("labware:"))  # tip racks are counted as tips, not twice
    reagents: Counter = Counter()
    for p in plans:
        for liq in p.liquids:
            if liq.source == "robot_loaded" and liq.reagent != "insert (synthesised gene fragment)":
                reagents[liq.reagent] += liq.volume_ul
    assert {k: v.quantity for k, v in q.items() if k.startswith("reagent:")} == {f"reagent:{k}": v for k, v in reagents.items()}
    assert all(v.unit == "uL" for k, v in q.items() if k.startswith("reagent:"))
    assert q["reagent:nuclease-free water"].quantity > 0 and set(q) >= {"reagent:wash buffer", "reagent:LAL reagent"}
    assert "animal" not in q  # the animal count belongs to the whole study, not to one protocol


def test_the_synthesised_insert_is_not_a_reagent_because_gene_synthesis_is_its_price(plans):
    """S9's assembly session loads the vendor fragment as a reagent under this name; listing it too would charge for the gene twice."""
    assembly = next(p for p in plans if p.session_id.endswith("plasmid_assembly"))
    assert any(liq.reagent == s8_cost.SYNTHESISED_INSERT and liq.source == "robot_loaded" for liq in assembly.liquids)  # S9 still uses this name
    assert f"reagent:{s8_cost.SYNTHESISED_INSERT}" not in by_item(protocol_quantities(plans))


def test_quantities_are_deterministic_and_merge_sums_equal_items_in_a_fixed_order(plans):
    a, b = protocol_quantities(plans), protocol_quantities(plan_sessions(build_protocol(CAND), RobotParams()))
    assert a == b
    merged = merge_quantities([a, b, [Quantity("animal", 10, "animal")]])
    assert by_item(merged)["gene_synthesis"].quantity == 2 and by_item(merged)["robot_session"].quantity == 2 * len(plans)
    assert [q.item for q in merged][:2] == ["gene_synthesis", "robot_session"] and merged[-1].item == "animal"
    groups = [q.item.split(":")[0] for q in merged]
    assert groups == sorted(groups, key=["gene_synthesis", "robot_session", "tip", "labware", "reagent", "animal"].index)
    assert [q.item for q in merged] == [q.item for q in merge_quantities([b, a, [Quantity("animal", 10, "animal")]])]


# =========================================================================== pricing: not_estimated / partial / estimated


QS = [Quantity("gene_synthesis", 3, "synthesis"), Quantity("tip:x", 100, "tip"), Quantity("reagent:water", 1500, "uL")]


def test_no_prices_means_not_estimated_with_every_quantity_listed():
    c = estimate(QS, {}, basis=BASIS)
    assert c.status == "not_estimated" and c.total is None and c.priced_subtotal is None and c.basis == BASIS
    assert [(i.item, i.quantity, i.unit) for i in c.items] == [(q.item, q.quantity, q.unit) for q in QS]
    assert all(i.unit_price is None and i.currency is None and i.subtotal is None for i in c.items)
    assert "No item is priced" in c.note and "streamc.prices" in c.note and "No price is built in" in c.note


def test_some_prices_mean_partial_with_the_unpriced_items_named_and_no_total():
    c = estimate(QS, parse_prices({"tip:x": price(0.25, "tip")}), basis=BASIS)
    assert c.status == "partial" and c.total is None
    assert c.priced_subtotal == CostTotal(currency="GBP", amount=25.0)  # 100 x 0.25
    got = by_item(c.items)
    assert (got["tip:x"].unit_price, got["tip:x"].subtotal, got["tip:x"].currency) == (0.25, 25.0, "GBP")
    assert got["gene_synthesis"].unit_price is None and got["reagent:water"].subtotal is None
    assert "2 of 3 items have no price" in c.note and "gene_synthesis" in c.note and "reagent:water" in c.note and "NOT a total" in c.note


def test_all_prices_mean_estimated_with_exact_arithmetic_including_unit_conversion():
    table = parse_prices({"gene_synthesis": price(50.1, "synthesis"), "tip:x": price(0.1, "tip"), "reagent:water": price(2.2, "mL")})
    c = estimate(QS, table, basis=BASIS)
    got = by_item(c.items)
    assert got["gene_synthesis"].subtotal == 150.3 and got["tip:x"].subtotal == 10.0 and got["reagent:water"].subtotal == 3.3  # 3 x 50.1, 100 x 0.1, 1.5 mL x 2.2
    assert got["reagent:water"].unit == "uL" and got["reagent:water"].unit_price == 0.0022  # restated per uL
    assert c.status == "estimated" and c.total == CostTotal(currency="GBP", amount=163.6) == c.priced_subtotal  # exact: 150.3 + 10.0 + 3.3
    assert "Every listed item is priced" in c.note and "nothing here verifies" in c.note


def test_a_price_per_litre_and_per_microlitre_are_converted_exactly():
    for unit, amount in (("L", 2000), ("l", 2000), ("mL", 2), ("uL", 0.002), ("µL", 0.002), ("μl", 0.002)):  # micro sign, Greek mu
        c = estimate([Quantity("reagent:water", 1500, "uL")], parse_prices({"reagent:water": price(amount, unit)}), basis=BASIS)
        assert c.status == "estimated" and c.total.amount == 3.0, unit


def test_a_price_of_zero_is_a_price_and_marks_an_item_as_counted_elsewhere():
    c = estimate(QS, parse_prices({"gene_synthesis": price(10, "synthesis"), "tip:x": price(0, "tip"), "reagent:water": price(0, "mL")}), basis=BASIS)
    assert c.status == "estimated" and c.total.amount == 30.0


def test_a_price_per_the_wrong_unit_is_a_config_error():
    with pytest.raises(ConfigError, match=r"per 'ml'.*counted in 'tip'"):
        estimate(QS, parse_prices({"tip:x": price(1, "mL")}), basis=BASIS)
    with pytest.raises(ConfigError, match=r"per 'rack'.*counted in 'tip'"):
        estimate(QS, parse_prices({"tip:x": price(1, "rack")}), basis=BASIS)
    with pytest.raises(ConfigError, match="uL, mL or L"):
        estimate(QS, parse_prices({"reagent:water": price(1, "kg")}), basis=BASIS)


def test_decimal_arithmetic_has_no_float_noise():
    c = estimate([Quantity("a", 3, "tip")], parse_prices({"a": price(0.1, "tip")}), basis=BASIS)
    assert c.total.amount == 0.3  # 3 x 0.1 in floats is 0.30000000000000004


def test_keys_that_match_no_item_are_reported_only_where_asked():
    table = parse_prices({"tip:x": price(1, "tip"), "tip:typo": price(1, "tip")})
    assert estimate(QS, table, basis=BASIS).unused_price_keys is None
    c = estimate(QS, table, basis=BASIS, report_unused=True)
    assert c.unused_price_keys == ["tip:typo"] and "match no item" in c.note and "tip:typo" in c.note
    assert estimate(QS, {}, basis=BASIS, report_unused=True).unused_price_keys == []


def test_estimating_is_deterministic():
    table = parse_prices({"tip:x": price(0.1, "tip")})
    assert estimate(QS, table, basis=BASIS) == estimate(list(QS), dict(table), basis=BASIS)


def test_gbp_range_only_for_a_fully_priced_estimate_in_gbp():
    full = lambda cur: estimate(QS, parse_prices({"gene_synthesis": price(1, "synthesis", cur), "tip:x": price(1, "tip", cur), "reagent:water": price(1, "mL", cur)}), basis=BASIS)  # noqa: E731
    assert s8_cost.gbp_range(full("GBP")) == (full("GBP").total.amount,) * 2
    assert s8_cost.gbp_range(full("USD")) is None
    assert s8_cost.gbp_range(estimate(QS, parse_prices({"tip:x": price(1, "tip")}), basis=BASIS)) is None  # partial
    assert s8_cost.gbp_range(estimate(QS, {}, basis=BASIS)) is None and s8_cost.gbp_range(None) is None


# =========================================================================== the stage


def test_with_an_empty_price_table_the_stage_says_not_estimated_and_still_lists_the_quantities(unpriced, plans):
    st, c, art = unpriced
    cost = art.payload["cost"]
    assert art.status == "ok" and cost["status"] == "not_estimated" and cost["total"] is None and cost["priced_subtotal"] is None
    assert cost["unused_price_keys"] == [] and art.params["prices"] == {}
    items = by_item(cost["items"])
    assert items["gene_synthesis"]["quantity"] == 2 and items["robot_session"]["quantity"] == 2 * len(plans)  # one synthesis per candidate; the same sessions each
    assert all(i["unit_price"] is None and i["subtotal"] is None and i["currency"] is None for i in items.values())
    assert any(k.startswith("tip:") for k in items) and any(k.startswith("labware:") for k in items) and any(k.startswith("reagent:") for k in items)
    for entry in art.payload["candidates"]:
        proto = Protocol.model_validate(entry["protocol"])
        assert proto.cost.status == "not_estimated" and proto.cost_gbp_range is None and "unsourced" in proto.estimate_basis
        own = by_item(proto.cost.items)
        assert own["gene_synthesis"].quantity == 1 and own["robot_session"].quantity == len(plans) and "animal" not in own
        assert proto.cost.unused_price_keys is None
        for key, i in own.items():  # the stage total is the sum of the protocols (+ the animals)
            assert items[key]["quantity"] == 2 * i.quantity


def test_the_stage_quantities_are_the_plans_of_each_protocol_summed(unpriced):
    st, c, art = unpriced
    inputs = load_inputs_from_store(st, c, top_k=2)
    expected = merge_quantities(protocol_quantities(plan_protocol(build_protocol(cand), RobotParams())) for cand in inputs.candidates)
    got = [(i["item"], i["quantity"], i["unit"]) for i in art.payload["cost"]["items"] if i["item"] != "animal"]
    assert got == [(q.item, q.quantity, q.unit) for q in expected]


def test_the_animal_item_is_the_s10_design_total_for_the_same_candidates(unpriced):
    st, c, art = unpriced
    inputs = load_inputs_from_store(st, c, top_k=2)
    design = s10_animal.design_study(inputs.candidates, inputs.pathogen, s10_animal.StudyConfig())
    animal = by_item(art.payload["cost"]["items"])["animal"]
    assert (animal["quantity"], animal["unit"]) == (design.power_analysis.total_animals, "animal") and animal["quantity"] > 0
    assert "total_animals" in art.payload["cost"]["note"] and art.payload["cost"]["items"][-1]["item"] == "animal"


def test_the_animal_item_is_omitted_not_guessed_when_s10_cannot_give_it(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("scipy is not installed")

    monkeypatch.setattr(s10_animal, "design_study", boom)
    art = s8_protocol.run(copy_run(tmp_path / "run"), cfg(proteins=["fhbp"], top_k=1))
    cost = art.payload["cost"]
    assert art.status == "ok" and "animal" not in by_item(cost["items"]) and cost["status"] == "not_estimated"
    assert "No animal item" in cost["note"] and "RuntimeError" in cost["note"]
    assert any("no animal item" in w and "RuntimeError" in w for w in art.warnings)


def all_prices(art, currency="GBP") -> dict:
    """A price for every item of an unpriced stage cost: reagents per mL, everything else per its own unit; distinct, awkward numbers."""
    table = {}
    for n, i in enumerate(art.payload["cost"]["items"], start=1):
        table[i["item"]] = price(round(0.37 * n, 2), "mL" if i["unit"] == "uL" else i["unit"], currency)
    return table


def expected_total(art, table) -> Decimal:
    total = Decimal(0)
    for i in art.payload["cost"]["items"]:
        p = table[i["item"]]
        total += Decimal(str(i["quantity"])) * Decimal(str(p["price"])) / (1000 if p["unit"] == "mL" else 1)
    return total


def test_with_every_item_priced_in_gbp_the_stage_is_estimated_and_each_protocol_gets_a_range(unpriced):
    st, c, art0 = unpriced
    table = all_prices(art0)
    art = s8_protocol.run(st, cfg(proteins=["fhbp", "nada"], top_k=2, prices=table))
    cost = art.payload["cost"]
    assert cost["status"] == "estimated" and cost["unused_price_keys"] == []
    total = expected_total(art0, table)
    assert cost["total"] == {"currency": "GBP", "amount": float(total)} == cost["priced_subtotal"]
    assert sum(Decimal(str(i["subtotal"])) for i in cost["items"]) == total  # the total is exactly the sum of its lines
    assert art.params["prices"] == {k: {"price": float(v["price"]), "currency": "GBP", "unit": v["unit"].lower()} for k, v in sorted(table.items())}
    ranges = []
    for entry in art.payload["candidates"]:
        proto = Protocol.model_validate(entry["protocol"])
        assert proto.cost.status == "estimated" and proto.cost.total.currency == "GBP"
        assert proto.cost_gbp_range == (proto.cost.total.amount, proto.cost.total.amount) and "PARTLY" not in proto.estimate_basis
        assert "x the prices you supplied" in proto.estimate_basis and "unsourced" not in proto.estimate_basis
        ranges.append(proto.cost.total.amount)
    # the stage total covers two protocols and the animals; each protocol alone covers its own items only
    assert round(sum(ranges) + by_item(cost["items"])["animal"]["subtotal"], 6) == pytest.approx(cost["total"]["amount"], abs=1e-6)


def test_with_some_items_priced_the_stage_is_partial_and_no_range_is_set(unpriced):
    st, c, art0 = unpriced
    tips = by_item(art0.payload["cost"]["items"])["tip:opentrons_96_tiprack_20ul"]["quantity"]
    art = s8_protocol.run(st, cfg(proteins=["fhbp", "nada"], top_k=2, prices={"gene_synthesis": price(100, "synthesis"), "tip:opentrons_96_tiprack_20ul": price(0.5, "tip")}))
    cost = art.payload["cost"]
    assert cost["status"] == "partial" and cost["total"] is None
    assert cost["priced_subtotal"] == {"currency": "GBP", "amount": float(Decimal(200) + Decimal(tips) * Decimal("0.5"))}  # 2 x 100 + the tips x 0.5
    assert "have no price" in cost["note"] and "NOT a total" in cost["note"]
    for entry in art.payload["candidates"]:
        assert entry["protocol"]["cost_gbp_range"] is None and entry["protocol"]["cost"]["status"] == "partial" and "PARTLY" in entry["protocol"]["estimate_basis"]


def test_a_fully_priced_estimate_in_another_currency_gets_no_gbp_range(unpriced):
    st, c, art0 = unpriced
    art = s8_protocol.run(st, cfg(proteins=["fhbp", "nada"], top_k=2, prices=all_prices(art0, "USD")))
    assert art.payload["cost"]["status"] == "estimated" and art.payload["cost"]["total"]["currency"] == "USD"
    assert all(e["protocol"]["cost"]["status"] == "estimated" and e["protocol"]["cost_gbp_range"] is None for e in art.payload["candidates"])


def test_the_stage_rejects_bad_tables(tmp_path):
    st = copy_run(tmp_path / "run")
    for bad in ({"tip:x": price(1, "tip", "GBP"), "gene_synthesis": price(1, "synthesis", "EUR")}, {"gene_synthesis": price(-1, "synthesis")}, "cheap"):
        with pytest.raises(ConfigError):
            s8_protocol.run(st, cfg(proteins=["fhbp"], top_k=1, prices=bad))
    with pytest.raises(ConfigError, match="counted in 'synthesis'"):
        s8_protocol.run(st, cfg(proteins=["fhbp"], top_k=1, prices={"gene_synthesis": price(1, "tip")}))
    assert not st.has("s8")  # nothing was written for a config that could not be used


def test_a_price_key_that_matches_no_item_is_reported(unpriced):
    st, c, _ = unpriced
    art = s8_protocol.run(st, cfg(proteins=["fhbp", "nada"], top_k=2, prices={"reagent:nuclease free water": price(1, "mL")}))
    assert art.payload["cost"]["unused_price_keys"] == ["reagent:nuclease free water"] and art.payload["cost"]["status"] == "not_estimated"


def test_the_digest_follows_the_prices_and_a_rerun_is_identical(unpriced):
    st, c, art0 = unpriced
    again = s8_protocol.run(st, c)
    assert again.digest == art0.digest  # deterministic: same inputs, same bytes
    priced = s8_protocol.run(st, cfg(proteins=["fhbp", "nada"], top_k=2, prices={"gene_synthesis": price(100, "synthesis")}))
    assert priced.digest != art0.digest and priced.params["prices"] != art0.params["prices"]
    assert s8_protocol.run(st, c).digest == art0.digest  # and back


def test_robot_params_that_cannot_be_planned_do_not_crash_s8_and_nothing_is_priced(tmp_path):
    st = copy_run(tmp_path / "run")
    art = s8_protocol.run(st, cfg(proteins=["fhbp"], top_k=1, robot_params={"assembly_total_ul": 5}, prices={"gene_synthesis": price(100, "synthesis")}))
    assert art.status == "ok" and art.payload["cost"]["status"] == "not_estimated" and art.payload["cost"]["items"] == []
    assert art.payload["cost"]["total"] is None and "cannot be built" in art.payload["cost"]["note"]
    proto = art.payload["candidates"][0]["protocol"]
    assert proto["cost"]["status"] == "not_estimated" and proto["cost"]["items"] == [] and proto["cost_gbp_range"] is None  # not "1 synthesis = estimated"
    assert any("cannot be built" in w for w in art.warnings)
    with pytest.raises(ConfigError, match="streamc.robot_params is not valid"):
        s8_protocol.run(st, cfg(proteins=["fhbp"], top_k=1, robot_params={"replicates": 9}))


def test_a_stage_with_nothing_to_do_has_no_cost(tmp_path):
    art = s8_protocol.run(copy_run(tmp_path / "run"), cfg())
    assert art.status == "ok" and art.payload["status"] == "not_run" and art.payload["cost"] is None


# =========================================================================== render_markdown


def costed(status: str) -> Protocol:
    p = build_protocol(CAND)
    items = [CostItem(item="gene_synthesis", quantity=1, unit="synthesis", unit_price=50.0, currency="GBP", subtotal=50.0),
             CostItem(item="tip:opentrons_96_tiprack_20ul", quantity=1234567, unit="tip", unit_price=0.002, currency="GBP", subtotal=2469.134)]
    if status == "not_estimated":
        items = [CostItem(item=i.item, quantity=i.quantity, unit=i.unit) for i in items]
        p.cost = CostEstimate(status=status, basis=BASIS, items=items, note="NOTE-N")
    elif status == "partial":
        items[1] = CostItem(item=items[1].item, quantity=items[1].quantity, unit=items[1].unit)
        p.cost = CostEstimate(status=status, basis=BASIS, items=items, priced_subtotal=CostTotal(currency="GBP", amount=50.0), note="NOTE-P")
    else:
        p.cost = CostEstimate(status=status, basis=BASIS, items=items, total=CostTotal(currency="GBP", amount=2519.134), priced_subtotal=CostTotal(currency="GBP", amount=2519.134), note="NOTE-E")
    return p


def test_markdown_has_no_cost_section_when_the_protocol_was_not_costed():
    md = render_markdown(build_protocol(CAND))
    assert HEADING not in md and "streamc.prices" not in md


@pytest.mark.parametrize("status, sentence", [
    ("not_estimated", "**Not estimated.** No price was supplied for any item."),
    ("partial", "**Partly priced.** Priced subtotal GBP 50.00 over 1 of 2 items - NOT a total: 1 item(s) have no price."),
    ("estimated", "**Total: GBP 2,519.13** - every item is priced"),
])
def test_markdown_renders_the_cost_section_for_each_status(status, sentence):
    p = costed(status)
    md = render_markdown(p)
    assert md.count(HEADING) == 1 and sentence in md
    assert md.index("## What this cascade does NOT measure") < md.index(HEADING) < md.index(f"_{p.estimate_basis}_")
    section = md.split(HEADING)[1]
    assert "PLACEHOLDER plan" in section and "none is built in" in section and f"NOTE-{status[0].upper()}" in section
    rows = [line for line in section.splitlines() if line.startswith("| ") and not line.startswith("| Item")]
    assert len(rows) == 2
    if status == "estimated":
        assert "| gene_synthesis | 1 | synthesis | GBP 50 | GBP 50.00 |" in section
        assert "| tip:opentrons_96_tiprack_20ul | 1,234,567 | tip | GBP 0.002 | GBP 2,469.13 |" in section
    else:
        assert "| gene_synthesis | 1 | synthesis |" in section and ("| - | - |" in section)
    assert md == render_markdown(p)  # deterministic


def test_markdown_keeps_the_sections_the_reviewer_tests_rely_on():
    md = render_markdown(costed("estimated"))
    assert len([line for line in md.splitlines() if line.startswith("| s")]) == 13  # the cost table adds no row that looks like a step row
    assert md.split("## Controls and gates")[1].split("## Falsification condition")[0].count(HEADING) == 0


def test_old_json_without_the_new_fields_still_loads_and_the_new_fields_round_trip():
    data = json.loads(s8_protocol.to_json(build_protocol(CAND)))
    old = copy.deepcopy(data)
    old.pop("cost")
    for s in old["steps"]:
        for k in ("quantities", "narrative", "narrative_source"):
            s.pop(k)
    p = Protocol.model_validate(old)
    assert p.cost is None and all(s.quantities == [] and s.narrative is None and s.narrative_source is None for s in p.steps)
    q = costed("estimated")
    assert Protocol.model_validate(json.loads(s8_protocol.to_json(q))).cost == q.cost
