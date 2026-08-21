"""Pure simulation helpers shared by the web app (no Streamlit / FastAPI)."""

from __future__ import annotations

import io

import pandas as pd

BRAND_DEFAULTS = {
    "coca-cola": {
        "label": "Coca-Cola",
        "rewards": {
            "8 Dollar Rebate": 5000,
            "Membership 9.99": 10000,
            "Reward 3 (TBD)": 15000,
        },
        "extra_cols": [],
    },
    "monster": {
        "label": "Monster",
        "rewards": {
            "Reward 1": 6500,
            "Reward 2": 10000,
            "Reward 3": 12000,
            "Reward 4": 15000,
            "Reward 5": 35000,
        },
        "extra_cols": ["size"],
    },
    "ferrera": {
        "label": "Ferrera",
        "rewards": {
            "Reward 1": 5000,
            "Reward 2": 10000,
            "Reward 3": 15000,
        },
        "extra_cols": ["brand", "category"],
    },
}


def compute_store_penetration(raw: pd.DataFrame, skus: set) -> pd.DataFrame:
    filtered = raw[raw["sku"].isin(skus)]
    pen = filtered.groupby("sku")["store_id"].nunique().reset_index()
    pen.columns = ["sku", "store_penetration"]
    return pen


def available_months(raw: pd.DataFrame, skus: set | None = None) -> list[str]:
    df = raw
    if skus is not None:
        df = raw[raw["sku"].isin(skus)]
    if df.empty:
        return []
    months = df["order_date"].dt.to_period("M").unique()
    return [str(p) for p in sorted(months)]


def get_month_orders(raw: pd.DataFrame, year: int, month: int, skus: set) -> pd.DataFrame:
    """Brand SKU rows for one calendar month. Stores with no such rows are absent."""
    start = pd.Timestamp(year, month, 1)
    if month == 12:
        end = pd.Timestamp(year + 1, 1, 1)
    else:
        end = pd.Timestamp(year, month + 1, 1)
    filtered = raw[(raw["order_date"] >= start) & (raw["order_date"] < end)]
    filtered = filtered[filtered["sku"].isin(skus)]
    return filtered.groupby(["store_id", "sku"])["total_quantity"].sum().reset_index()


def simulate(month_orders, sku_to_title, points_lookup, reward_thresholds) -> pd.DataFrame:
    """Score each store that appears in month_orders. One unit is enough; no min qty."""
    df = month_orders.copy()
    df["product_title"] = df["sku"].map(sku_to_title)
    df["points_per_unit"] = df["product_title"].map(points_lookup).fillna(0)
    df["points_earned"] = df["total_quantity"] * df["points_per_unit"]

    store_points = df.groupby("store_id").agg(
        total_points=("points_earned", "sum"),
        total_units=("total_quantity", "sum"),
        distinct_skus=("sku", "nunique"),
    ).reset_index()

    for reward_name, threshold in reward_thresholds.items():
        store_points[reward_name] = store_points["total_points"] >= threshold

    return store_points


def parse_imported_points(file_bytes: bytes, filename: str):
    name = filename.lower()
    buffer = io.BytesIO(file_bytes)
    if name.endswith(".csv"):
        df = pd.read_csv(buffer)
    elif name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(buffer)
    else:
        return None, "Unsupported file type. Please upload a CSV or Excel file."

    df.columns = df.columns.str.strip().str.lower()
    if "sku" not in df.columns or "points" not in df.columns:
        return None, f"File must contain 'sku' and 'points' columns. Found: {', '.join(df.columns)}"

    df = df[["sku", "points"]].dropna(subset=["sku"])
    df["sku"] = df["sku"].astype(str).str.strip()
    result = {}
    for _, row in df.iterrows():
        try:
            result[str(row["sku"])] = int(float(row["points"]))
        except (ValueError, TypeError):
            result[str(row["sku"])] = 0
    return result, None


def build_points_lookup(skus_df: pd.DataFrame, proposed: dict[str, int]) -> dict:
    """Map product_title -> points, using proposed overrides when > 0."""
    lookup = {}
    for _, row in skus_df.iterrows():
        title = row["product_title"]
        current = int(row.get("current_points") or 0)
        override = proposed.get(str(row["sku"]))
        if override is None:
            override = proposed.get(title)
        if override is not None and int(override) > 0:
            lookup[title] = int(override)
        else:
            lookup[title] = current
    return lookup


def summarize_results(store_points: pd.DataFrame, reward_thresholds: dict) -> dict:
    """Metrics and reward % use the full store_points population (current-month orderers).

    The histogram clips the 99th percentile for chart bins only. The store
    table is the top 500 by points; totals are not clipped or capped.
    """
    total_stores = len(store_points)
    if total_stores == 0:
        return {
            "total_stores": 0,
            "avg_points": 0,
            "median_points": 0,
            "max_points": 0,
            "rewards": [],
            "stores": [],
            "histogram": [],
        }

    rewards = []
    for name, threshold in reward_thresholds.items():
        count = int(store_points[name].sum()) if name in store_points.columns else 0
        pct = count / total_stores * 100 if total_stores else 0
        rewards.append({
            "name": name,
            "threshold": int(threshold),
            "count": count,
            "pct": round(pct, 1),
        })

    hist_series = store_points["total_points"].clip(
        upper=store_points["total_points"].quantile(0.99)
    )
    bins = min(40, max(10, int(hist_series.nunique())))
    counts, edges = pd.cut(hist_series, bins=bins, retbins=True)
    hist = hist_series.groupby(counts, observed=False).count()
    histogram = []
    for interval, count in hist.items():
        if hasattr(interval, "left"):
            histogram.append({
                "label": f"{int(interval.left)}-{int(interval.right)}",
                "count": int(count),
                "mid": float((interval.left + interval.right) / 2),
            })

    display_cols = ["store_id", "total_points", "total_units", "distinct_skus"] + list(reward_thresholds.keys())
    stores_df = store_points[display_cols].sort_values("total_points", ascending=False)
    stores = []
    for _, row in stores_df.head(500).iterrows():
        entry = {
            "store_id": row["store_id"],
            "total_points": int(row["total_points"]),
            "total_units": int(row["total_units"]),
            "distinct_skus": int(row["distinct_skus"]),
            "rewards": {name: bool(row[name]) for name in reward_thresholds},
        }
        stores.append(entry)

    return {
        "total_stores": total_stores,
        "avg_points": float(store_points["total_points"].mean()),
        "median_points": float(store_points["total_points"].median()),
        "max_points": float(store_points["total_points"].max()),
        "rewards": rewards,
        "stores": stores,
        "histogram": histogram,
    }
