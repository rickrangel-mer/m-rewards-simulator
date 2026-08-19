from pathlib import Path

import pandas as pd

from data import NUM_MONTHS, load_sku_mapping, read_orders

OUTPUT_DIR = Path(__file__).parent


# ---------------------------------------------------------------------------
# Computation: cohort assignment
# ---------------------------------------------------------------------------

def assign_store_cohorts(order_data: pd.DataFrame, rewards_skus: set) -> dict:
    stores_with_rewards = set(
        order_data.loc[order_data["sku"].isin(rewards_skus), "store_id"]
    )
    all_stores = set(order_data["store_id"])
    return {
        store: "rewards-eligible" if store in stores_with_rewards else "coca-cola-only"
        for store in all_stores
    }


# ---------------------------------------------------------------------------
# Computation: SKU-level metrics
# ---------------------------------------------------------------------------

def compute_sku_metrics(
    order_data: pd.DataFrame,
    sku_mapping: pd.DataFrame,
    store_cohorts: dict,
) -> pd.DataFrame:
    df = order_data.copy()
    df["cohort"] = df["store_id"].map(store_cohorts)
    df["month"] = df["order_date"].dt.to_period("M")

    grouped = df.groupby(["sku", "cohort"]).agg(
        total_units=("total_quantity", "sum"),
        store_penetration=("store_id", "nunique"),
        num_orders=("total_quantity", "count"),
        months_with_orders=("month", "nunique"),
    ).reset_index()

    store_months = df.groupby(["sku", "cohort", "store_id"])["month"].nunique().reset_index(name="store_month_count")
    avg_freq = store_months.groupby(["sku", "cohort"])["store_month_count"].mean().reset_index(name="ordering_frequency")
    avg_freq["ordering_frequency"] = avg_freq["ordering_frequency"] / NUM_MONTHS

    grouped = grouped.merge(avg_freq, on=["sku", "cohort"], how="left")
    grouped["avg_monthly_units"] = grouped["total_units"] / NUM_MONTHS
    grouped["avg_qty_per_order"] = grouped["total_units"] / grouped["num_orders"]
    grouped.drop(columns=["num_orders"], inplace=True)

    grouped = grouped.merge(sku_mapping, on="sku", how="right")
    grouped["no_order_data"] = grouped["cohort"].isna()

    no_data = grouped[grouped["no_order_data"]].drop_duplicates(subset=["sku"])
    has_data = grouped[~grouped["no_order_data"]]

    for col in ["total_units", "store_penetration", "avg_monthly_units", "ordering_frequency", "avg_qty_per_order"]:
        no_data[col] = 0

    return pd.concat([has_data, no_data], ignore_index=True)


# ---------------------------------------------------------------------------
# Computation: store-level summary
# ---------------------------------------------------------------------------

def compute_store_summary(
    order_data: pd.DataFrame,
    rewards_skus: set,
    store_cohorts: dict,
) -> pd.DataFrame:
    df = order_data.copy()
    df["cohort"] = df["store_id"].map(store_cohorts)
    df["is_rewards_sku"] = df["sku"].isin(rewards_skus)

    store_totals = df.groupby("store_id").agg(
        total_coke_units=("total_quantity", "sum"),
        distinct_coke_skus=("sku", "nunique"),
    ).reset_index()

    rewards_df = df[df["is_rewards_sku"]]
    rewards_agg = rewards_df.groupby("store_id").agg(
        total_rewards_units=("total_quantity", "sum"),
        distinct_rewards_skus=("sku", "nunique"),
    ).reset_index()

    non_rewards_agg = df[~df["is_rewards_sku"]].groupby("store_id").agg(
        total_non_rewards_units=("total_quantity", "sum"),
    ).reset_index()

    result = store_totals.merge(rewards_agg, on="store_id", how="left")
    result = result.merge(non_rewards_agg, on="store_id", how="left")
    result["total_rewards_units"] = result["total_rewards_units"].fillna(0).astype(int)
    result["distinct_rewards_skus"] = result["distinct_rewards_skus"].fillna(0).astype(int)
    result["total_non_rewards_units"] = result["total_non_rewards_units"].fillna(0).astype(int)
    result["cohort"] = result["store_id"].map(store_cohorts)

    return result


# ---------------------------------------------------------------------------
# Computation: monthly trend
# ---------------------------------------------------------------------------

def compute_monthly_trend(
    order_data: pd.DataFrame,
    rewards_skus: set,
    store_cohorts: dict,
) -> pd.DataFrame:
    df = order_data.copy()
    df["cohort"] = df["store_id"].map(store_cohorts)
    df["sku_type"] = df["sku"].apply(lambda s: "rewards" if s in rewards_skus else "non-rewards")
    df["month"] = df["order_date"].dt.to_period("M").astype(str)

    result = df.groupby(["month", "cohort", "sku_type"]).agg(
        total_units=("total_quantity", "sum"),
        num_orders=("total_quantity", "count"),
        distinct_stores=("store_id", "nunique"),
        distinct_skus=("sku", "nunique"),
    ).reset_index()

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Loading SKU mapping from Excel...")
    sku_mapping = load_sku_mapping()
    print(f"  {len(sku_mapping)} SKUs loaded ({sku_mapping['rewards_match'].notna().sum()} rewards)")

    rewards_skus = set(sku_mapping.loc[sku_mapping["rewards_match"].notna(), "sku"])

    print("Loading order data from Postgres...")
    order_data = read_orders()
    print(f"  {len(order_data):,} order rows, {order_data['store_id'].nunique():,} stores")

    print("Assigning store cohorts...")
    store_cohorts = assign_store_cohorts(order_data, rewards_skus)
    re_count = sum(1 for v in store_cohorts.values() if v == "rewards-eligible")
    co_count = sum(1 for v in store_cohorts.values() if v == "coca-cola-only")
    print(f"  Rewards-eligible: {re_count:,} stores | Coca-Cola only: {co_count:,} stores")

    print("Computing SKU metrics...")
    sku_metrics = compute_sku_metrics(order_data, sku_mapping, store_cohorts)
    sku_metrics.to_csv(OUTPUT_DIR / "sku_monthly_behavior.csv", index=False)
    print(f"  Wrote local analysis copy sku_monthly_behavior.csv ({len(sku_metrics)} rows)")

    print("Computing store summaries...")
    store_summary = compute_store_summary(order_data, rewards_skus, store_cohorts)
    store_summary.to_csv(OUTPUT_DIR / "store_cohort_summary.csv", index=False)
    print(f"  Wrote local analysis copy store_cohort_summary.csv ({len(store_summary)} rows)")

    print("Computing monthly trends...")
    monthly_trend = compute_monthly_trend(order_data, rewards_skus, store_cohorts)
    monthly_trend.to_csv(OUTPUT_DIR / "monthly_trend.csv", index=False)
    print(f"  Wrote local analysis copy monthly_trend.csv ({len(monthly_trend)} rows)")

    print("\nDone. Source of truth is Postgres; CSV dumps are local analysis artifacts only.")
