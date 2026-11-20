import pandas as pd
import pytest

from forecast import (
    DEFAULT_HORIZON,
    behavior_profile,
    forecast_launch,
    launch_effect,
    load_benchmark,
    monthly_points,
    parse_settings,
    segment_month,
    settings_query,
)


def _orders(rows):
    """rows: (store, sku, 'YYYY-MM', units)"""
    return pd.DataFrame({
        "store_id": [r[0] for r in rows],
        "sku": [r[1] for r in rows],
        "order_date": pd.to_datetime([f"{r[2]}-15" for r in rows]),
        "total_quantity": [r[3] for r in rows],
    })


def _steady(store="S1", sku="A", units=100, months=6):
    return [(store, sku, f"2026-{m:02d}", units) for m in range(1, months + 1)]


def _settings(**kw):
    out = {"lifts": {}, "redeem_pct": 100, "horizon": 3}
    out.update(kw)
    return out


def test_segment_month_tags_each_behavior():
    rows = _steady("REG", "A", 10)                       # buys A every month
    rows += [("REG", "B", "2026-06", 5)]                  # B is new for REG in June
    rows += [("OLD", "A", "2026-01", 5), ("OLD", "A", "2026-06", 5)]  # back after a gap
    rows += [("NEW", "A", "2026-06", 5)]                  # first order ever
    monthly = monthly_points(_orders(rows), {"A": 10, "B": 20})
    seg = segment_month(monthly, "2026-06").set_index(["store_id", "sku"])["segment"]
    assert seg[("REG", "A")] == "regulars"
    assert seg[("REG", "B")] == "new_items"
    assert seg[("OLD", "A")] == "returning"
    assert seg[("NEW", "A")] == "new_stores"


def test_profile_needs_three_prior_months():
    monthly = monthly_points(_orders(_steady(months=3)), {"A": 10})
    assert behavior_profile(monthly, "2026-03") is None
    assert forecast_launch(monthly, "2026-03", [("R", 100, 100)], _settings()) is None


def test_skus_without_points_are_ignored():
    monthly = monthly_points(_orders(_steady() + [("S1", "X", "2026-06", 999)]), {"A": 10})
    assert set(monthly["sku"]) == {"A"}


def test_points_carry_over_and_redeem_spends_cheapest_first():
    # 2,000 points a month; rewards cost 3,000 / 5,000. Everyone who can redeems.
    monthly = monthly_points(_orders(_steady(units=200)), {"A": 10})
    rewards = [("Big", 5000, 2500), ("Small", 3000, 800)]
    fc = forecast_launch(monthly, "2026-06", rewards, _settings(horizon=4))
    rows = fc["months"]
    assert [r["issued_total"] for r in rows] == [2000, 2000, 2000, 2000]
    # M1: 2,000 < 3,000. M2: 4,000 -> Small (1,000 left). M3: 3,000 -> Small. M4: 2,000.
    assert [r["redeemed"] for r in rows] == [0, 3000, 3000, 0]
    assert [r["outstanding"] for r in rows] == [2000, 1000, 0, 2000]
    assert fc["payout_cents"] == 1600
    assert {r["name"]: r["claims"] for r in fc["rewards"]} == {"Small": 2, "Big": 0}
    # Cheapest first in the table, too.
    assert [r["name"] for r in fc["rewards"]] == ["Small", "Big"]


def test_redeeming_store_claims_everything_it_can_afford():
    # 16,000 points in one month: claims 3,000 + 5,000 + 7,500, keeps 500.
    monthly = monthly_points(_orders(_steady(units=1600)), {"A": 10})
    rewards = [("Hat", 3000, 3000), ("Hoodie", 5000, 5000), ("Kit", 7500, 15000)]
    fc = forecast_launch(monthly, "2026-06", rewards, _settings(horizon=1))
    assert fc["months"][0]["redeemed"] == 15500
    assert fc["outstanding"] == 500
    assert fc["payout_cents"] == 23000


def test_no_redemption_leaves_every_point_in_the_market():
    monthly = monthly_points(_orders(_steady(units=1000)), {"A": 10})
    fc = forecast_launch(monthly, "2026-06", [("R", 3000, 1000)], _settings(redeem_pct=0, horizon=6))
    assert fc["payout_cents"] == 0
    assert fc["outstanding"] == fc["issued"] == 60000
    # Unredeemed points are valued at the rewards' own dollars per point.
    assert fc["outstanding_cents"] == 20000


def test_regulars_lift_scales_their_points_and_is_reported_as_lift():
    monthly = monthly_points(_orders(_steady(units=100)), {"A": 10})
    flat = forecast_launch(monthly, "2026-06", [], _settings())
    lifted = forecast_launch(monthly, "2026-06", [], _settings(lifts={"regulars": 50}))
    assert flat["issued"] == 3000
    assert lifted["issued"] == 4500
    regulars = next(b for b in lifted["behaviors"] if b["key"] == "regulars")
    assert regulars["lift_points"] == 1500
    assert lifted["without_lift"]["issued"] == 3000
    assert lifted["no_rewards"] is True


def test_new_store_lifts_add_and_remove_stores():
    rows = _steady("REG", "A", 10)
    # Ten first-time stores in each of April, May and June.
    for month in ("04", "05", "06"):
        rows += [(f"N{month}{k}", "A", f"2026-{month}", 10) for k in range(10)]
    monthly = monthly_points(_orders(rows), {"A": 10})
    base = forecast_launch(monthly, "2026-06", [], _settings())
    more = forecast_launch(monthly, "2026-06", [], _settings(lifts={"new_stores": 100}))
    fewer = forecast_launch(monthly, "2026-06", [], _settings(lifts={"new_stores": -100}))
    bigger = forecast_launch(monthly, "2026-06", [], _settings(lifts={"new_store_basket": 50}))

    def new_points(fc):
        return next(b for b in fc["behaviors"] if b["key"] == "new_stores")["points"]

    assert new_points(base) == 3 * 10 * 100
    assert new_points(more) == 2 * new_points(base)
    assert new_points(fewer) == 0
    assert new_points(bigger) == pytest.approx(1.5 * new_points(base))


def test_forecast_is_repeatable():
    rows = []
    for k in range(40):
        rows += [(f"S{k}", "A", f"2026-{m:02d}", 30 + k) for m in range(1, 7) if (k + m) % 3]
    monthly = monthly_points(_orders(rows), {"A": 100})
    rewards = [("R", 3000, 500)]
    a = forecast_launch(monthly, "2026-06", rewards, _settings(redeem_pct=30, horizon=6))
    b = forecast_launch(monthly, "2026-06", rewards, _settings(redeem_pct=30, horizon=6))
    assert a["months"] == b["months"]
    assert 0 < a["payout_cents"]
    assert a["outstanding"] > 0


def test_launch_effect_counts_only_moves_outside_the_normal_range():
    rows = []
    # 20 regular stores each month; new stores: 2, 3, 2 in Apr-Jun, then 10 in July.
    for m in range(1, 8):
        rows += [(f"R{k}", "A", f"2026-{m:02d}", 10) for k in range(20)]
    # New stores keep ordering after their first month, so regulars stay steady.
    for first, n in ((4, 2), (5, 3), (6, 2), (7, 10)):
        rows += [(f"N{first}-{k}", "A", f"2026-{m:02d}", 10) for k in range(n) for m in range(first, 8)]
    monthly = monthly_points(_orders(rows), {"A": 10})
    effect = launch_effect(monthly, "2026-07")
    assert effect["normal_months"] == ["2026-06", "2026-05", "2026-04"]
    new_stores = effect["lifts"]["new_stores"]
    assert new_stores["outside_normal"] is True
    assert new_stores["default_pct"] > 100
    # Regulars bought exactly as before.
    assert effect["lifts"]["regulars"]["outside_normal"] is False
    assert effect["lifts"]["regulars"]["default_pct"] == 0


def test_settings_default_to_benchmark_and_clamp():
    bench = {"lifts": {"new_store_basket": {"default_pct": 76}}, "redeem_pct": 9.9}
    s = parse_settings({}, bench)
    assert s["lifts"]["new_store_basket"] == 76
    assert s["lifts"]["regulars"] == 0
    assert s["redeem_pct"] == 9.9
    assert s["horizon"] == DEFAULT_HORIZON
    assert settings_query(s, bench) == []

    s = parse_settings({"fc_regulars": "25", "fc_redeem": "150", "fc_months": "12", "fc_new_items": "junk"}, bench)
    assert s["lifts"]["regulars"] == 25
    assert s["lifts"]["new_items"] == 0
    assert s["redeem_pct"] == 100
    assert s["horizon"] == 12
    assert ("fc_regulars", "25") in settings_query(s, bench)
    assert ("fc_months", "12") in settings_query(s, bench)
    assert parse_settings({"fc_months": "5"}, bench)["horizon"] == DEFAULT_HORIZON


def test_checked_in_red_bull_benchmark_loads():
    bench = load_benchmark()
    assert bench["brand_name"] == "Red Bull"
    assert bench["launch_month"] == "2026-10"
    assert 0 < bench["redeem_pct"] < 100
    assert set(bench["lifts"]) == {"regulars", "new_items", "returning", "new_stores", "new_store_basket"}
