"""Rent-as-profit estimate — where does a tenant's rent actually go?

A teaching/advocacy model (from a Southside Together tenant-organizing activity, by way of
Sean MacMullan) that decomposes a building's rent into the four things it pays for:

    rent  =  property tax  +  mortgage (debt service)  +  operating costs  +  landlord profit

and reports ``profit_share = profit / rent`` — the slice of every rent dollar that is the
landlord's profit rather than taxes, debt, or upkeep.

**This is an estimate, not an audit.** Property tax is grounded in the parcel's actual assessed
value, but rent (an area median), the mortgage (modeled from the last sale price), and operating
costs (a rule of thumb) are *modeled*. Every assumption lives in :class:`ProfitAssumptions` so it
is explicit and tunable — change a lever, see the breakdown move. Profit can come out negative
(an underwater, over-leveraged, or recently-bought landlord); that is a real and honest result,
not an error.

The model is deliberately self-contained (standard library only) so it doubles as the calculator
Sean imagined and as the per-parcel core of the aggregation pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class ProfitAssumptions:
    """Every modeled lever, in one place. Defaults are documented, conservative rules of thumb —
    override any of them per run (or per area) to test how the answer responds.

    The two *certain* inputs (rent and assessed value) are passed to :func:`estimate_building`
    directly; everything here is a modeling assumption.
    """

    # --- Rent (ACS area estimate → building) ----------------------------------------------
    occupancy: float = 0.95
    """Share of units occupied and paying. 5% vacancy/loss is a standard underwrite."""

    # --- Property tax (assessed value → annual bill) --------------------------------------
    # Cook County mechanics: assessed value (AV) × state equalizer = equalized AV (EAV);
    # taxable EAV × composite tax rate = the bill. Rentals carry no homeowner exemption, so for
    # absentee parcels taxable EAV ≈ EAV — convenient and on the conservative (higher-tax) side.
    state_equalizer: float = 3.0068
    """Illinois multiplier applied to Cook County AV (2023 final ≈ 3.0068; published yearly)."""
    composite_tax_rate: float = 0.069
    """Local composite rate on EAV. Chicago tax codes run ~6.5–7.5%; 6.9% is a city midpoint."""
    assessment_level: float = 0.10
    """Class-2 residential AV is 10% of market value, so market value ≈ AV ÷ 0.10."""

    # --- Mortgage / debt service (sale price → annual payment) ----------------------------
    loan_to_value: float = 0.75
    """Assumed financing at purchase. 25% down is a common small-multifamily investor figure."""
    mortgage_rate: float = 0.065
    """Annual interest rate on the assumed loan."""
    mortgage_term_years: int = 30
    """Amortization term, in years."""

    # --- Operating costs (non-tax) --------------------------------------------------------
    operating_cost_ratio: float = 0.30
    """Maintenance, insurance, utilities, management, and turnover as a share of rent —
    EXCLUDING property tax, which is broken out separately so it is not double-counted. The
    real-estate "50% rule" bundles tax in at ~50% of rent; tax is typically ~15–20% of rent,
    which leaves ~30% non-tax. Tune per appetite."""


def mortgage_constant(annual_rate: float, term_years: int) -> float:
    """Annual debt service per $1 of loan (the "mortgage constant"): the level yearly payment
    that fully amortizes a $1 loan at ``annual_rate`` over ``term_years``. A 0% loan returns
    simple 1/term repayment."""
    n = term_years * 12
    r = annual_rate / 12
    if r == 0:
        monthly = 1 / n
    else:
        monthly = r * (1 + r) ** n / ((1 + r) ** n - 1)
    return monthly * 12


def estimate_property_tax(assessed_value: float, a: ProfitAssumptions) -> float:
    """Annual property-tax bill modeled from the parcel's assessed value (AV → EAV → bill)."""
    return assessed_value * a.state_equalizer * a.composite_tax_rate


def estimate_market_value(assessed_value: float, a: ProfitAssumptions) -> float:
    """Estimated market value implied by the assessed value (AV ÷ assessment level)."""
    if a.assessment_level <= 0:
        return 0.0
    return assessed_value / a.assessment_level


def estimate_building(
    *,
    monthly_rent_per_unit: float,
    units: int,
    assessed_value: float,
    sale_price: float | None = None,
    assumptions: ProfitAssumptions | None = None,
) -> dict[str, Any]:
    """Decompose one building's annual rent into tax / mortgage / operating / profit.

    Certain inputs:
      * ``monthly_rent_per_unit`` — area median gross rent (ACS B25064), per unit.
      * ``units`` — units in the building (assessor characteristics; ≥1).
      * ``assessed_value`` — the parcel's total assessed value (assessor certified_tot).
    Modeled input:
      * ``sale_price`` — last arm's-length sale, the mortgage basis. If absent, the AV-implied
        market value is used instead (clearly the weaker branch — labeled in ``basis``).

    Returns a breakdown of annual dollars plus each component's share of rent. ``profit_share``
    is ``None`` when there is no rent to divide by. Profit may be negative.
    """
    a = assumptions or ProfitAssumptions()
    units = max(1, int(units))

    annual_rent = monthly_rent_per_unit * units * 12 * a.occupancy
    property_tax = estimate_property_tax(assessed_value, a)

    if sale_price and sale_price > 0:
        mortgage_basis, basis = float(sale_price), "last_sale_price"
    else:
        mortgage_basis, basis = estimate_market_value(assessed_value, a), "assessed_value_implied"
    debt_service = mortgage_basis * a.loan_to_value * mortgage_constant(a.mortgage_rate, a.mortgage_term_years)

    operating_costs = annual_rent * a.operating_cost_ratio
    profit = annual_rent - property_tax - debt_service - operating_costs

    def share(x: float) -> float | None:
        return round(x / annual_rent, 4) if annual_rent > 0 else None

    return {
        "units": units,
        "annual_rent": round(annual_rent, 2),
        "property_tax": round(property_tax, 2),
        "mortgage": round(debt_service, 2),
        "operating_costs": round(operating_costs, 2),
        "profit": round(profit, 2),
        "profit_share": share(profit),
        "shares": {
            "property_tax": share(property_tax),
            "mortgage": share(debt_service),
            "operating_costs": share(operating_costs),
            "profit": share(profit),
        },
        "mortgage_basis": basis,
        "estimated_market_value": round(estimate_market_value(assessed_value, a), 2),
    }


def with_assumptions(base: ProfitAssumptions | None = None, **overrides: Any) -> ProfitAssumptions:
    """Convenience: a copy of ``base`` (or defaults) with named levers overridden."""
    return replace(base or ProfitAssumptions(), **overrides)
