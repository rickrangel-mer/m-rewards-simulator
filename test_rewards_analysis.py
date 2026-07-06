import pandas as pd
import pytest


@pytest.fixture
def sample_order_data():
    """Two stores, two SKUs, across 3 months. S1 is rewards-eligible, S2 is coca-cola-only."""
    return pd.DataFrame({
        "store_id": ["S1", "S1", "S1", "S1", "S2", "S2"],
        "sku":      ["SKU-R1", "SKU-R1", "SKU-R1", "SKU-X1", "SKU-X1", "SKU-X1"],
        "order_date": pd.to_datetime([
            "2026-01-10", "2026-02-10", "2026-03-10",  # S1 orders SKU-R1 three months
            "2026-01-15",                                # S1 orders SKU-X1 once
            "2026-01-20", "2026-02-20",                  # S2 orders SKU-X1 two months
        ]),
        "total_quantity": [4, 6, 2, 10, 3, 9],
    })


@pytest.fixture
def sample_sku_mapping():
    return pd.DataFrame({
        "sku": ["SKU-R1", "SKU-X1", "SKU-Z1"],
        "product_title": ["Coke Zero 20oz", "Monster Energy 12oz", "Fanta Orange 20oz"],
        "category": ["SSD", "Energy", "SSD"],
        "rewards_match": ["YES", None, None],
        "points": [500, None, None],
    })


def test_store_cohort_assignment_separates_rewards_and_non_rewards_stores():
    from rewards_analysis import assign_store_cohorts

    order_data = pd.DataFrame({
        "store_id": ["S1", "S1", "S2", "S2", "S3"],
        "sku": ["SKU-R1", "SKU-X1", "SKU-X1", "SKU-X2", "SKU-R1"],
        "order_date": pd.to_datetime(["2026-01-15"] * 5),
        "total_quantity": [1, 2, 3, 1, 1],
    })
    rewards_skus = {"SKU-R1", "SKU-R2"}

    cohorts = assign_store_cohorts(order_data, rewards_skus)

    assert cohorts["S1"] == "rewards-eligible"
    assert cohorts["S2"] == "coca-cola-only"
    assert cohorts["S3"] == "rewards-eligible"


def test_sku_metrics_computes_five_metrics_per_cohort(sample_order_data, sample_sku_mapping):
    from rewards_analysis import assign_store_cohorts, compute_sku_metrics

    rewards_skus = {"SKU-R1"}
    store_cohorts = assign_store_cohorts(sample_order_data, rewards_skus)
    result = compute_sku_metrics(sample_order_data, sample_sku_mapping, store_cohorts)

    # SKU-R1: only S1 orders it (rewards-eligible cohort), 3 orders over 3 months, quantities 4+6+2=12
    r1_re = result[(result["sku"] == "SKU-R1") & (result["cohort"] == "rewards-eligible")]
    assert len(r1_re) == 1
    row = r1_re.iloc[0]
    assert row["total_units"] == 12
    assert row["store_penetration"] == 1
    assert row["avg_monthly_units"] == pytest.approx(12 / 6)  # 12 units / 6 months
    assert row["ordering_frequency"] == pytest.approx(3 / 6)  # 3 out of 6 months
    assert row["avg_qty_per_order"] == pytest.approx(4.0)  # 12 / 3 orders

    # SKU-R1 should have no coca-cola-only row (S2 never ordered it)
    r1_co = result[(result["sku"] == "SKU-R1") & (result["cohort"] == "coca-cola-only")]
    assert len(r1_co) == 0

    # SKU-X1: S1 (rewards-eligible) ordered 1 time qty=10; S2 (coca-cola-only) ordered 2 times qty=3+9=12
    x1_re = result[(result["sku"] == "SKU-X1") & (result["cohort"] == "rewards-eligible")]
    assert x1_re.iloc[0]["total_units"] == 10
    assert x1_re.iloc[0]["store_penetration"] == 1

    x1_co = result[(result["sku"] == "SKU-X1") & (result["cohort"] == "coca-cola-only")]
    assert x1_co.iloc[0]["total_units"] == 12
    assert x1_co.iloc[0]["store_penetration"] == 1
    assert x1_co.iloc[0]["avg_qty_per_order"] == pytest.approx(6.0)  # 12 / 2 orders

    # SKU metadata should be present
    assert row["rewards_match"] == "YES"
    assert row["points"] == 500
    assert row["category"] == "SSD"

    # SKU-Z1 should be flagged as no order data
    z1 = result[result["sku"] == "SKU-Z1"]
    assert len(z1) == 1
    assert z1.iloc[0]["no_order_data"] == True
    assert z1.iloc[0]["total_units"] == 0


def test_store_summary_computes_per_store_totals(sample_order_data, sample_sku_mapping):
    from rewards_analysis import assign_store_cohorts, compute_store_summary

    rewards_skus = {"SKU-R1"}
    store_cohorts = assign_store_cohorts(sample_order_data, rewards_skus)
    result = compute_store_summary(sample_order_data, rewards_skus, store_cohorts)

    s1 = result[result["store_id"] == "S1"].iloc[0]
    assert s1["cohort"] == "rewards-eligible"
    assert s1["total_coke_units"] == 22  # 4+6+2+10
    assert s1["total_rewards_units"] == 12  # 4+6+2
    assert s1["total_non_rewards_units"] == 10
    assert s1["distinct_coke_skus"] == 2  # SKU-R1, SKU-X1
    assert s1["distinct_rewards_skus"] == 1  # SKU-R1

    s2 = result[result["store_id"] == "S2"].iloc[0]
    assert s2["cohort"] == "coca-cola-only"
    assert s2["total_coke_units"] == 12  # 3+9
    assert s2["total_rewards_units"] == 0
    assert s2["total_non_rewards_units"] == 12
    assert s2["distinct_coke_skus"] == 1
    assert s2["distinct_rewards_skus"] == 0


def test_monthly_trend_aggregates_by_month_cohort_rewards_status(sample_order_data, sample_sku_mapping):
    from rewards_analysis import assign_store_cohorts, compute_monthly_trend

    rewards_skus = {"SKU-R1"}
    store_cohorts = assign_store_cohorts(sample_order_data, rewards_skus)
    result = compute_monthly_trend(sample_order_data, rewards_skus, store_cohorts)

    # Jan 2026: S1 orders SKU-R1 qty=4 (rewards-eligible, rewards), S1 orders SKU-X1 qty=10 (rewards-eligible, non-rewards), S2 orders SKU-X1 qty=3 (coca-cola-only, non-rewards)
    jan_re_rewards = result[
        (result["month"] == "2026-01") & (result["cohort"] == "rewards-eligible") & (result["sku_type"] == "rewards")
    ]
    assert jan_re_rewards.iloc[0]["total_units"] == 4

    jan_re_non = result[
        (result["month"] == "2026-01") & (result["cohort"] == "rewards-eligible") & (result["sku_type"] == "non-rewards")
    ]
    assert jan_re_non.iloc[0]["total_units"] == 10

    jan_co_non = result[
        (result["month"] == "2026-01") & (result["cohort"] == "coca-cola-only") & (result["sku_type"] == "non-rewards")
    ]
    assert jan_co_non.iloc[0]["total_units"] == 3

    # Feb 2026: S1 orders SKU-R1 qty=6, S2 orders SKU-X1 qty=9
    feb_re_rewards = result[
        (result["month"] == "2026-02") & (result["cohort"] == "rewards-eligible") & (result["sku_type"] == "rewards")
    ]
    assert feb_re_rewards.iloc[0]["total_units"] == 6
