"""S8 cost: QUANTITIES from the S9 placeholder plans x PRICES the user supplies. No price is built in, remembered or guessed.

README S8 asks for an estimated cost. The honest version is a bill of quantities with the unit prices left to the person who knows them:

  quantities   derived from `plan_sessions(protocol, RobotParams)`. Every RobotParams number is a PLACEHOLDER, so every quantity is one.
                 gene_synthesis         one per candidate                                 unit "synthesis"
                 robot_session          one per S9 session                                unit "session"
                 tip:<tip rack>         tips used (an 8-channel pick-up is 8 tips)        unit "tip"
                 labware:<load name>    labware placed on a deck (tip racks are tips)     unit "item"
                 reagent:<name>         volume the robot draws from a stock, incl. the    unit "uL"
                                        dead volume (what an operator has to prepare)
                 animal                 total animals of the S10 design for the shortlist unit "animal" (only when S10's design can be computed)
  prices       config `streamc.prices`: item key -> {price, currency, unit}. Nothing else is a source. One currency for the whole table.
               `unit` must be the unit of the quantity above; reagent prices may also be per mL or per L (exact SI conversion).

Result (`CostEstimate`): `not_estimated` (no item priced), `partial` (some priced; the unpriced items are named and no total is given)
or `estimated` (every item priced). Money is added as decimals, so a total is exactly the sum of the subtotals.

Not counted, because nothing here knows it: equipment, kits or consumables that a person (not the robot) uses, vectors and cells,
labour, shipping, tax. A sample that an earlier step produced (e.g. "candidate sample (endotoxin-reduced)") is listed as a reagent because
the S9 plan loads it from a tube; price it 0 to say "counted elsewhere". The synthesised insert is NOT listed as a reagent: it is the
product of `gene_synthesis`, and listing both would charge for it twice.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from vaxforge.config import ConfigError
from vaxforge.s9_plan import PIPETTES, SessionPlan
from vaxforge.streamc_adapter import setting
from vaxforge.streamc_models import CostEstimate, CostItem, CostTotal

# Units of the quantities (what a price must be "per").
UNIT_SYNTHESIS, UNIT_SESSION, UNIT_TIP, UNIT_ITEM, UNIT_UL, UNIT_ANIMAL = "synthesis", "session", "tip", "item", "uL", "animal"
GENE_SYNTHESIS, ROBOT_SESSION, ANIMAL = "gene_synthesis", "robot_session", "animal"
TIP_PREFIX, LABWARE_PREFIX, REAGENT_PREFIX = "tip:", "labware:", "reagent:"

# S9's assembly session loads the vendor's synthesised fragment as a robot-loaded reagent under this name (s9_sessions.build_assembly).
# Its cost is `gene_synthesis`; tests/wetlab/test_s8_cost.py checks that the name is still the one S9 uses.
SYNTHESISED_INSERT = "insert (synthesised gene fragment)"

PRICE_FIELDS = ("price", "currency", "unit")
_VOLUME_UL = {"ul": Decimal(1), "ml": Decimal(1000), "l": Decimal(1000000)}  # price units accepted for reagent quantities, in uL


@dataclass(frozen=True)
class Quantity:
    item: str
    quantity: int | float
    unit: str


@dataclass(frozen=True)
class Price:
    price: Decimal
    currency: str
    unit: str  # as normalised by `_unit_key`


def _unit_key(unit: str) -> str:
    return unit.strip().lower().replace("µ", "u").replace("μ", "u")  # micro sign and Greek mu -> u


# --------------------------------------------------------------------------- the price table (user-supplied)


def parse_prices(raw: Any) -> dict[str, Price]:
    """`streamc.prices` -> {item key: Price}. Raises ConfigError for anything that is not exactly {price, currency, unit} per item,
    a negative or non-finite price, a currency that is not a 3-letter code, or more than one currency (no conversion is ever done)."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ConfigError(f"streamc.prices must be a mapping of item key -> {{price, currency, unit}}, got {type(raw).__name__}")
    table: dict[str, Price] = {}
    for key, entry in raw.items():
        where = f"streamc.prices[{key!r}]"
        if not isinstance(key, str) or not key.strip():
            raise ConfigError(f"streamc.prices has a key that is not a non-empty string: {key!r}")
        if not isinstance(entry, Mapping):
            raise ConfigError(f"{where} must be a mapping with {', '.join(PRICE_FIELDS)}, got {type(entry).__name__}")
        missing, unknown = [f for f in PRICE_FIELDS if f not in entry], sorted(str(k) for k in entry if k not in PRICE_FIELDS)
        if missing or unknown:
            raise ConfigError(f"{where} needs exactly {', '.join(PRICE_FIELDS)}" + (f"; missing {missing}" if missing else "") + (f"; unknown {unknown}" if unknown else ""))
        value = entry["price"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ConfigError(f"{where}.price must be a finite number >= 0, got {value!r}")
        currency, unit = entry["currency"], entry["unit"]
        if not isinstance(currency, str) or len(currency.strip()) != 3 or not currency.strip().isalpha():
            raise ConfigError(f"{where}.currency must be a 3-letter code such as GBP, got {currency!r}")
        if not isinstance(unit, str) or not _unit_key(unit):
            raise ConfigError(f"{where}.unit must say what the price is per (e.g. 'tip', 'mL'), got {unit!r}")
        try:
            amount = Decimal(str(value))
        except InvalidOperation:  # pragma: no cover - finite int/float always parse
            raise ConfigError(f"{where}.price is not a number: {value!r}") from None
        table[key] = Price(amount, currency.strip().upper(), _unit_key(unit))
    currencies = sorted({p.currency for p in table.values()})
    if len(currencies) > 1:
        raise ConfigError(f"streamc.prices mixes currencies {currencies}: use one currency for the whole table (no conversion is done)")
    return table


def prices_from(config: Any) -> dict[str, Price]:
    """The user's price table from config `streamc.prices` (absent or empty -> no prices: nothing is priced)."""
    return parse_prices(setting(config, "prices"))


def prices_as_params(table: Mapping[str, Price]) -> dict[str, dict[str, Any]]:
    """The table as JSON for the stage's `params` (so a different table gives a different digest)."""
    return {k: {"price": float(p.price), "currency": p.currency, "unit": p.unit} for k, p in sorted(table.items())}


# --------------------------------------------------------------------------- quantities (from the S9 placeholder plans)


def protocol_quantities(plans: Iterable[SessionPlan]) -> list[Quantity]:
    """What ONE protocol needs: its gene synthesis, its robot sessions, and the tips, labware and reagent volumes of those sessions."""
    plans = list(plans)
    tips: dict[str, int] = {}
    labware: dict[str, int] = {}
    reagents: dict[str, float] = {}
    for plan in plans:
        for role, pickups in plan.tips_needed.items():
            _api, _mount, _lo, _hi, channels, rack = PIPETTES[role]
            tips[rack] = tips.get(rack, 0) + pickups * channels
        for lw in plan.labware:
            if lw.kind != "tiprack":  # tip racks are counted as tips
                labware[lw.load_name] = labware.get(lw.load_name, 0) + 1
        for liq in plan.liquids:
            if liq.source == "robot_loaded" and liq.reagent != SYNTHESISED_INSERT:
                reagents[liq.reagent] = reagents.get(liq.reagent, 0.0) + liq.volume_ul
    return merge_quantities([[
        Quantity(GENE_SYNTHESIS, 1, UNIT_SYNTHESIS),
        Quantity(ROBOT_SESSION, len(plans), UNIT_SESSION),
        *(Quantity(TIP_PREFIX + k, v, UNIT_TIP) for k, v in tips.items()),
        *(Quantity(LABWARE_PREFIX + k, v, UNIT_ITEM) for k, v in labware.items()),
        *(Quantity(REAGENT_PREFIX + k, _tidy(v), UNIT_UL) for k, v in reagents.items()),
    ]])


def _tidy(x: float) -> int | float:
    return int(x) if float(x).is_integer() else x


def _group(item: str) -> int:
    for rank, prefix in enumerate((GENE_SYNTHESIS, ROBOT_SESSION, TIP_PREFIX, LABWARE_PREFIX, REAGENT_PREFIX, ANIMAL)):
        if item == prefix or item.startswith(prefix):
            return rank
    return 99  # pragma: no cover - every item this module makes has one of the prefixes above


def merge_quantities(lists: Iterable[Iterable[Quantity]]) -> list[Quantity]:
    """Sum equal items over several lists (the protocols of a shortlist). The order is fixed: synthesis, sessions, tips, labware, reagents, animals."""
    total: dict[str, Quantity] = {}
    for qs in lists:
        for q in qs:
            have = total.get(q.item)
            if have is None:
                total[q.item] = q
            elif have.unit != q.unit:
                raise ValueError(f"{q.item}: quantities in different units ({have.unit} and {q.unit})")  # pragma: no cover - cannot happen from one catalogue
            else:
                total[q.item] = Quantity(q.item, _tidy(have.quantity + q.quantity), q.unit)
    return sorted(total.values(), key=lambda q: (_group(q.item), q.item))


def animal_quantity(candidates: list[Any], pathogen: Any, config: Any) -> tuple[Quantity | None, str | None]:
    """The animals of the S10 design for these candidates (`total_animals`), computed here exactly as S10 computes it, from the same
    `streamc.study` settings. Returns (None, why) when it cannot be computed: the cost then simply has no animal item, never a guess."""
    try:
        from vaxforge import s10_animal  # imported here: it needs scipy for the power analysis, and S8 must work without it

        design = s10_animal.design_study(list(candidates), pathogen, s10_animal.study_config_from(config))
        return Quantity(ANIMAL, int(design.power_analysis.total_animals), UNIT_ANIMAL), None
    except Exception as exc:  # noqa: BLE001 - an optional extra: whatever stops it, S8 goes on without the item and says why
        return None, f"the S10 animal count could not be computed ({type(exc).__name__}: {' '.join(str(exc).split())[:160]})"


# --------------------------------------------------------------------------- pricing


def _unit_price_per_item_unit(q: Quantity, price: Price) -> Decimal:
    """The user's price expressed per unit of `q` (uL for reagents). Raises ConfigError if the price is per something else."""
    want = _unit_key(q.unit)
    if want == "ul":
        if price.unit in _VOLUME_UL:
            return price.price / _VOLUME_UL[price.unit]
    elif price.unit == want:
        return price.price
    raise ConfigError(
        f"streamc.prices[{q.item!r}] is per {price.unit!r}, but this item is counted in {q.unit!r}"
        + (" (a reagent price may be per uL, mL or L)" if want == "ul" else "") + ": restate the price per that unit")


def _money(x: Decimal) -> float:
    return float(x)


def estimate(quantities: Iterable[Quantity], prices: Mapping[str, Price], *, basis: str, report_unused: bool = False, extra_note: str = "") -> CostEstimate:
    """Price `quantities` with `prices` (and nothing else). Status: `estimated` only if every item is priced."""
    quantities = list(quantities)
    items: list[CostItem] = []
    subtotal = Decimal(0)
    n_priced = 0
    currency: str | None = None
    for q in quantities:
        price = prices.get(q.item)
        if price is None:
            items.append(CostItem(item=q.item, quantity=q.quantity, unit=q.unit))
            continue
        per_unit = _unit_price_per_item_unit(q, price)
        line = Decimal(str(q.quantity)) * per_unit
        subtotal += line
        n_priced += 1
        currency = price.currency
        items.append(CostItem(item=q.item, quantity=q.quantity, unit=q.unit, unit_price=_money(per_unit), currency=price.currency, subtotal=_money(line)))
    n = len(items)
    unpriced = [i.item for i in items if i.unit_price is None]
    scope = ("Only the listed items are counted: no equipment, no kit or consumable that a person (not the robot) uses, no vector or cells, "
             "no labour, shipping or tax.")
    if n_priced == 0:
        status, total, priced = "not_estimated", None, None
        note = ("No item is priced, so there is no estimate: set streamc.prices (item key -> {price, currency, unit}); the keys are the `item` values "
                "listed here. No price is built in.")
    elif n_priced < n:
        status, total, priced = "partial", None, CostTotal(currency=currency or "", amount=_money(subtotal))
        shown = ", ".join(unpriced[:6]) + (f", ... ({len(unpriced) - 6} more)" if len(unpriced) > 6 else "")
        note = (f"{len(unpriced)} of {n} items have no price (first: {shown}). The priced subtotal covers only the {n_priced} priced items and "
                "is NOT a total; add the missing keys to streamc.prices (price 0 marks an item as counted elsewhere).")
    else:
        status, total = "estimated", CostTotal(currency=currency or "", amount=_money(subtotal))
        priced = total
        note = "Every listed item is priced from streamc.prices, which the user supplied and nothing here verifies; the quantities are placeholders."
    unused = sorted(set(prices) - {q.item for q in quantities}) if report_unused else None
    if unused:
        note += f" {len(unused)} price key(s) match no item (check the spelling): {', '.join(unused[:6])}" + (", ..." if len(unused) > 6 else "") + "."
    return CostEstimate(status=status, basis=basis, items=items, total=total, priced_subtotal=priced, unused_price_keys=unused,
                        note=" ".join(x for x in (note, extra_note, scope) if x))


def not_estimated(basis: str, why: str) -> CostEstimate:
    """No quantities could be derived, so there is nothing to price (never a total from a part of the plan)."""
    return CostEstimate(status="not_estimated", basis=basis, items=[], total=None, priced_subtotal=None, note=why)


def basis_text(n_protocols: int) -> str:
    return (f"Quantities: the S9 placeholder plans (s9_sessions.RobotParams: coarse defaults, not validated for any kit, cell line or assay) for "
            f"{n_protocols} shortlisted protocol(s). Prices: only those in config streamc.prices, as supplied by the user; none is built in.")


def estimate_basis_for(cost: CostEstimate | None, default: str) -> str:
    """`Protocol.estimate_basis` once a cost estimate exists: durations stay estimates, and cost says what it rests on."""
    if cost is None or cost.status == "not_estimated":
        return default
    if cost.status == "partial":
        return "Durations are coarse engineering estimates, not measured; cost is only PARTLY priced: placeholder quantities x the prices you supplied, with items still unpriced."
    return "Durations are coarse engineering estimates, not measured; cost is placeholder quantities x the prices you supplied (streamc.prices), not a vendor quote."


def gbp_range(cost: CostEstimate | None) -> tuple[float, float] | None:
    """`Protocol.cost_gbp_range`: set only for a fully priced estimate in GBP. One price per item was given, so the range is a point (low == high)."""
    if cost is not None and cost.status == "estimated" and cost.total is not None and cost.total.currency == "GBP":
        return (cost.total.amount, cost.total.amount)
    return None
