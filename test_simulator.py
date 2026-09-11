from datetime import date

import pandas as pd

from simulator import (
    budget_from_results,
    build_points_lookup,
    dollars_to_cents,
    format_usd,
    get_month_orders,
    orders_for_period,
    parse_imported_points,
    parse_grain,
    period_copy,
    period_months,
    simulate,
    sku_point_totals,
    summarize_results,
)


def test_get_month_orders_filters_month_and_skus():
    raw = pd.DataFrame({
        "store_id": ["S1", "S1", "S2"],
        "sku": ["A", "A", "B"],
        "order_date": pd.to_datetime(["2026-07-05", "2026-08-05", "2026-08-10"]),
        "total_quantity": [2, 3, 4],
    })
    result = get_month_orders(raw, 2026, 8, {"A", "B"})
    assert len(result) == 2
    assert result.loc[result["sku"] == "A", "total_quantity"].iloc[0] == 3
    assert result.loc[result["sku"] == "B", "total_quantity"].iloc[0] == 4


def test_simulate_applies_points_and_reward_flags():
    orders = pd.DataFrame({
        "store_id": ["S1", "S1", "S2"],
        "sku": ["A", "B", "A"],
        "total_quantity": [10, 2, 1],
        "product_title": ["Prod A", "Prod B", "Prod A"],
    })
    points = {"Prod A": 100, "Prod B": 50}
    thresholds = {"Reward 1": 500, "Reward 2": 1200}
    result = simulate(orders, {"A": "Prod A", "B": "Prod B"}, points, thresholds)

    s1 = result[result["store_id"] == "S1"].iloc[0]
    assert s1["total_points"] == 1100  # 10*100 + 2*50
    assert bool(s1["Reward 1"]) is True
    assert bool(s1["Reward 2"]) is False


def test_build_points_lookup_prefers_proposed_sku_override():
    skus = pd.DataFrame({
        "sku": ["A", "B"],
        "product_title": ["Prod A", "Prod B"],
        "current_points": [10, 20],
    })
    lookup = build_points_lookup(skus, {"A": 99})
    assert lookup["Prod A"] == 99
    assert lookup["Prod B"] == 20


def test_parse_imported_points_csv():
    csv = b"sku,points\nA,100\nB,200\n"
    mapping, error = parse_imported_points(csv, "points.csv")
    assert error is None
    assert mapping == {"A": 100, "B": 200}


def test_parse_imported_points_ignores_extra_columns():
    csv = b"sku,Product Title ,Package Size,points\nDW12419-24,Red Bull,24,50\n"
    mapping, error = parse_imported_points(csv, "sales.csv")
    assert error is None
    assert mapping == {"DW12419-24": 50}


def test_summarize_results_empty():
    empty = pd.DataFrame(columns=["store_id", "total_points", "total_units", "distinct_skus"])
    summary = summarize_results(empty, {"Reward 1": 100})
    assert summary["total_stores"] == 0
    assert summary["rewards"] == []


def test_get_month_orders_keeps_qty_one_and_drops_other_month_or_sku():
    raw = pd.DataFrame({
        "store_id": ["S1", "S2", "S3", "S4"],
        "sku": ["A", "A", "B", "A"],
        "order_date": pd.to_datetime(["2026-08-01", "2026-07-15", "2026-08-10", "2026-08-20"]),
        "total_quantity": [1, 50, 9, 3],
    })
    result = get_month_orders(raw, 2026, 8, {"A"})
    assert set(result["store_id"]) == {"S1", "S4"}
    assert result.loc[result["store_id"] == "S1", "total_quantity"].iloc[0] == 1


def test_simulate_omits_stores_with_no_brand_rows_this_month():
    orders = pd.DataFrame({
        "store_id": ["S1"],
        "sku": ["A"],
        "total_quantity": [1],
        "product_title": ["Prod A"],
    })
    result = simulate(orders, {"A": "Prod A"}, {"Prod A": 10}, {"Reward 1": 100})
    assert list(result["store_id"]) == ["S1"]
    assert result.iloc[0]["total_points"] == 10
    assert bool(result.iloc[0]["Reward 1"]) is False


def test_summarize_results_histogram_clip_does_not_change_totals():
    n = 100
    points = list(range(n - 1)) + [1_000_000]
    store_points = pd.DataFrame({
        "store_id": [f"S{i}" for i in range(n)],
        "total_points": points,
        "total_units": [1] * n,
        "distinct_skus": [1] * n,
        "Reward 1": [p >= 50 for p in points],
    })
    summary = summarize_results(store_points, {"Reward 1": 50})
    assert summary["total_stores"] == n
    assert summary["max_points"] == 1_000_000
    assert summary["avg_points"] == sum(points) / n
    assert summary["rewards"][0]["count"] == sum(p >= 50 for p in points)
    hist_high = max(bin["mid"] for bin in summary["histogram"])
    assert hist_high < 1_000_000


def test_summarize_results_detail_caps_at_500_metrics_use_full_population():
    n = 501
    store_points = pd.DataFrame({
        "store_id": [f"S{i}" for i in range(n)],
        "total_points": list(range(n)),
        "total_units": [1] * n,
        "distinct_skus": [1] * n,
        "Reward 1": [True] * n,
    })
    summary = summarize_results(store_points, {"Reward 1": 0})
    assert summary["total_stores"] == 501
    assert len(summary["stores"]) == 500
    assert summary["rewards"][0]["count"] == 501
    assert summary["rewards"][0]["pct"] == 100.0
    assert summary["stores"][0]["store_id"] == "S500"


def test_parse_grain_defaults_to_month():
    assert parse_grain(None) == "month"
    assert parse_grain("") == "month"
    assert parse_grain("MONTH") == "month"
    assert parse_grain("quarter") == "quarter"
    assert parse_grain("Quarter") == "quarter"


def test_period_months_quarter_uses_complete_months_only():
    today = date(2026, 9, 11)
    assert period_months(2026, 7, "month", today=today) == [(2026, 7)]
    assert period_months(2026, 7, "quarter", today=today) == [(2026, 7), (2026, 8)]
    assert period_months(2026, 4, "quarter", today=today) == [(2026, 4), (2026, 5), (2026, 6)]


def test_orders_for_period_quarter_combines_units_and_keeps_july_only_store():
    raw = pd.DataFrame({
        "store_id": ["S1", "S1", "S2"],
        "sku": ["A", "A", "A"],
        "order_date": pd.to_datetime(["2026-07-05", "2026-08-05", "2026-07-10"]),
        "total_quantity": [2, 3, 4],
    })
    monthly = orders_for_period(raw, 2026, 7, {"A"}, grain="month")
    quarterly = orders_for_period(
        raw, 2026, 7, {"A"}, grain="quarter", today=date(2026, 9, 11)
    )
    assert set(monthly["store_id"]) == {"S1", "S2"}
    assert set(quarterly["store_id"]) == {"S1", "S2"}
    assert int(monthly.loc[monthly["store_id"] == "S1", "total_quantity"].iloc[0]) == 2
    assert int(quarterly.loc[quarterly["store_id"] == "S1", "total_quantity"].iloc[0]) == 5
    assert int(quarterly.loc[quarterly["store_id"] == "S2", "total_quantity"].iloc[0]) == 4


def test_period_copy_quarter_incomplete_q3():
    copy = period_copy("quarter", 2026, 7, [(2026, 7), (2026, 8)], today=date(2026, 9, 11))
    assert copy["period_label"] == "Q3 2026"
    assert "July–August" in copy["using_data"]
    assert "September is not complete" in copy["using_data"]
    assert copy["inclusion"] == "stores that ordered this brand this quarter"
    assert copy["incomplete"] is True


def test_budget_from_results_multiplies_earners_by_cents():
    results = {
        "rewards": [{"name": "Rebate", "count": 2, "threshold": 100, "pct": 100.0}],
    }
    budget = budget_from_results(results, [("Rebate", 100, 800)])
    assert budget["total_cents"] == 1600
    assert budget["total_label"] == "$16"
    assert budget["reward_lines"][0]["count"] == 2
    assert budget["reward_lines"][0]["value_label"] == "$8"


def test_sku_point_totals_units_times_proposed():
    orders = pd.DataFrame({
        "store_id": ["S1", "S2"],
        "sku": ["A", "A"],
        "total_quantity": [5, 3],
        "product_title": ["Prod A", "Prod A"],
    })
    skus = pd.DataFrame({
        "sku": ["A"],
        "product_title": ["Prod A"],
        "current_points": [50],
    })
    totals = sku_point_totals(orders, skus, {"A": 100})
    assert totals["total_units"] == 8
    assert totals["total_points_issued"] == 800
    assert totals["skus"][0]["points_per_unit"] == 100


def test_dollars_to_cents_and_format():
    assert dollars_to_cents("8") == 800
    assert dollars_to_cents("8.50") == 850
    assert dollars_to_cents("") == 0
    assert format_usd(1600) == "$16"
    assert format_usd(850) == "$8.50"


def test_redeem_counts_exclusive_lowest_and_highest():
    from simulator import redeem_counts

    store_points = pd.DataFrame({
        "store_id": ["S1", "S2"],
        "total_points": [400, 150],
        "Low": [True, True],
        "High": [True, False],
    })
    rewards = [("Low", 100, 800), ("High", 400, 2000)]
    assert redeem_counts(store_points, rewards, "all") == {"Low": 2, "High": 1}
    assert redeem_counts(store_points, rewards, "lowest") == {"Low": 2, "High": 0}
    assert redeem_counts(store_points, rewards, "highest") == {"Low": 1, "High": 1}


def test_available_quarters_from_months():
    from simulator import available_quarters

    qs = available_quarters(["2026-07", "2026-08", "2026-04"])
    assert [row["label"] for row in qs] == ["Q3 2026", "Q2 2026"]
    assert qs[0]["month"] == "2026-07"
