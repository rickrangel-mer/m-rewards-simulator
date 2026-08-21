import pandas as pd

from simulator import (
    build_points_lookup,
    get_month_orders,
    parse_imported_points,
    simulate,
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
