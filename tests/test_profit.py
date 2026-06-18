
from rentrate.profit import (
    ProfitAssumptions,
    estimate_building,
    estimate_market_value,
    estimate_property_tax,
    mortgage_constant,
    with_assumptions,
)


def test_mortgage_constant_matches_amortization_formula():
    # 6.5% / 30yr ≈ 0.0758 annual debt service per $1 of loan.
    assert abs(mortgage_constant(0.065, 30) - 0.075846) < 1e-4
    # A 0% loan is just straight-line repayment over the term.
    assert abs(mortgage_constant(0.0, 30) - 1 / 30) < 1e-9


def test_property_tax_follows_cook_county_mechanics():
    a = ProfitAssumptions()
    # AV $30k → EAV (×equalizer) → × composite rate.
    assert abs(estimate_property_tax(30000, a) - 30000 * a.state_equalizer * a.composite_tax_rate) < 1e-6
    # Class-2 AV is 10% of market value.
    assert estimate_market_value(30000, a) == 300000


def test_breakdown_is_an_exact_decomposition_of_rent():
    out = estimate_building(monthly_rent_per_unit=1200, units=3, assessed_value=40000, sale_price=400000)
    # rent = tax + mortgage + operating + profit, exactly (the whole point of the model).
    assert abs(out["annual_rent"] - (out["property_tax"] + out["mortgage"] + out["operating_costs"] + out["profit"])) < 0.05
    # The four shares of rent sum to 1.
    assert abs(sum(out["shares"].values()) - 1.0) < 1e-3
    assert out["units"] == 3
    assert out["mortgage_basis"] == "last_sale_price"


def test_recent_high_leverage_purchase_can_lose_money():
    # A 3-flat bought recently for $700k at low rents is underwater — an honest negative result.
    out = estimate_building(monthly_rent_per_unit=900, units=3, assessed_value=70000, sale_price=700000)
    assert out["profit"] < 0
    assert out["profit_share"] < 0


def test_long_held_low_basis_landlord_profits():
    # Same building, owned free of a big mortgage (no recent sale, modest assessed value): healthy profit.
    out = estimate_building(monthly_rent_per_unit=1500, units=3, assessed_value=25000, sale_price=None)
    assert out["mortgage_basis"] == "assessed_value_implied"
    assert out["profit"] > 0
    assert 0 < out["profit_share"] < 1


def test_single_family_defaults_to_one_unit():
    out = estimate_building(monthly_rent_per_unit=2000, units=0, assessed_value=35000, sale_price=350000)
    assert out["units"] == 1


def test_zero_rent_yields_no_profit_share():
    out = estimate_building(monthly_rent_per_unit=0, units=2, assessed_value=20000, sale_price=200000)
    assert out["annual_rent"] == 0
    assert out["profit_share"] is None


def test_assumptions_are_tunable():
    cheap = with_assumptions(operating_cost_ratio=0.10, mortgage_rate=0.03)
    dear = with_assumptions(operating_cost_ratio=0.45, mortgage_rate=0.09)
    base = dict(monthly_rent_per_unit=1300, units=4, assessed_value=50000, sale_price=500000)
    assert estimate_building(**base, assumptions=cheap)["profit"] > estimate_building(**base, assumptions=dear)["profit"]
