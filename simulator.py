"""Pure simulation helpers shared by the web app (no Streamlit / FastAPI)."""

from __future__ import annotations

import io
from datetime import date

import pandas as pd
from dateutil.relativedelta import relativedelta

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


def parse_grain(value: str | None) -> str:
    if (value or "").strip().lower() == "quarter":
        return "quarter"
    return "month"


def quarter_of(year: int, month: int) -> int:
    return (int(month) - 1) // 3 + 1


def months_in_quarter(year: int, quarter: int) -> list[tuple[int, int]]:
    start = (int(quarter) - 1) * 3 + 1
    return [(int(year), start + i) for i in range(3)]


def last_complete_month(today: date | None = None) -> tuple[int, int]:
    today = today or date.today()
    start = today.replace(day=1) - relativedelta(months=1)
    return start.year, start.month


def period_months(
    year: int,
    month: int,
    grain: str = "month",
    today: date | None = None,
) -> list[tuple[int, int]]:
    """Calendar months in the selected period. Quarter uses complete months only."""
    grain = parse_grain(grain)
    if grain != "quarter":
        return [(int(year), int(month))]
    today = today or date.today()
    last_y, last_m = last_complete_month(today)
    last_idx = last_y * 12 + last_m
    return [
        (y, m)
        for y, m in months_in_quarter(year, quarter_of(year, month))
        if y * 12 + m <= last_idx
    ]


def orders_for_period(
    raw: pd.DataFrame,
    year: int,
    month: int,
    skus: set,
    grain: str = "month",
    today: date | None = None,
) -> pd.DataFrame:
    """Brand SKU units for a month or the complete months of that month's quarter.

    One combined store/SKU frame so callers run a single simulate() on the period.
    """
    months = period_months(year, month, grain, today=today)
    empty = pd.DataFrame(columns=["store_id", "sku", "total_quantity"])
    if not months:
        return empty
    frames = [get_month_orders(raw, y, m, skus) for y, m in months]
    combined = pd.concat(frames, ignore_index=True) if frames else empty
    if combined is None or combined.empty:
        return empty
    return combined.groupby(["store_id", "sku"], as_index=False)["total_quantity"].sum()


def _month_name(year: int, month: int) -> str:
    return date(int(year), int(month), 1).strftime("%B")


def period_copy(
    grain: str,
    year: int,
    month: int,
    months: list[tuple[int, int]] | None = None,
    today: date | None = None,
) -> dict:
    """Plain-language labels for the simulation caption and hero."""
    grain = parse_grain(grain)
    if grain != "quarter":
        label = date(int(year), int(month), 1).strftime("%B %Y")
        return {
            "grain": "month",
            "period_label": label,
            "using_data": f"Using {label} ordering data",
            "inclusion": "stores that ordered this brand this month",
            "incomplete": False,
        }

    q = quarter_of(year, month)
    qlabel = f"Q{q} {year}"
    months = list(months) if months is not None else period_months(year, month, "quarter", today=today)
    names = [_month_name(y, m) for y, m in months]
    full = months_in_quarter(year, q)
    incomplete = months != full
    if not names:
        using = f"Using {qlabel} ordering data (no complete months yet)"
    else:
        span = names[0] if len(names) == 1 else f"{names[0]}–{names[-1]}"
        if incomplete:
            missing = [_month_name(y, m) for y, m in full if (y, m) not in months]
            if len(missing) == 1:
                using = f"Using {qlabel} ordering data ({span}; {missing[0]} is not complete)"
            else:
                using = f"Using {qlabel} ordering data ({span}; {', '.join(missing)} are not complete)"
        else:
            using = f"Using {qlabel} ordering data ({span})"
    return {
        "grain": "quarter",
        "period_label": qlabel,
        "using_data": using,
        "inclusion": "stores that ordered this brand this quarter",
        "incomplete": incomplete,
    }


def normalize_reward(item) -> tuple[str, int, int]:
    """Coerce (name, points) or (name, points, value_cents) to a 3-tuple."""
    name = str(item[0])
    pts = int(item[1])
    cents = int(item[2]) if len(item) > 2 else 0
    return name, pts, cents


def reward_thresholds(rewards) -> dict[str, int]:
    return {name: pts for name, pts, _cents in (normalize_reward(r) for r in rewards)}


def dollars_to_cents(value) -> int:
    text = str(value if value is not None else "").strip()
    if not text:
        return 0
    try:
        return int(round(float(text) * 100))
    except (TypeError, ValueError):
        return 0


def cents_to_dollar_input(cents) -> str:
    try:
        cents = int(cents)
    except (TypeError, ValueError):
        cents = 0
    if cents % 100 == 0:
        return str(cents // 100)
    return f"{cents / 100:.2f}"


def format_usd(cents) -> str:
    try:
        cents = int(round(float(cents)))
    except (TypeError, ValueError):
        cents = 0
    negative = cents < 0
    cents = abs(cents)
    if cents % 100 == 0:
        text = f"${cents // 100:,}"
    else:
        text = f"${cents / 100:,.2f}"
    return f"-{text}" if negative else text


def budget_from_results(results: dict, rewards) -> dict:
    """Estimated program cost: earners in this period × each reward's dollar value."""
    counts = {r["name"]: int(r.get("count") or 0) for r in results.get("rewards") or []}
    lines = []
    total_cents = 0
    for name, pts, cents in (normalize_reward(r) for r in rewards):
        count = counts.get(name, 0)
        line_cents = count * cents
        lines.append({
            "name": name,
            "threshold": pts,
            "count": count,
            "value_cents": cents,
            "value_label": format_usd(cents),
            "total_cents": line_cents,
            "total_label": format_usd(line_cents),
        })
        total_cents += line_cents
    return {
        "reward_lines": lines,
        "total_cents": total_cents,
        "total_label": format_usd(total_cents),
    }


def sku_point_totals(period_orders: pd.DataFrame, skus_df: pd.DataFrame, proposed: dict[str, int] | None = None) -> dict:
    """Units × proposed points (falling back to current points) per catalog SKU."""
    proposed = proposed or {}
    lookup = build_points_lookup(skus_df, proposed) if skus_df is not None and not skus_df.empty else {}
    units: dict[str, float] = {}
    if period_orders is not None and not period_orders.empty:
        grouped = period_orders.groupby(period_orders["sku"].astype(str))["total_quantity"].sum()
        units = {str(k): float(v) for k, v in grouped.items()}
    rows = []
    total_points = 0.0
    total_units = 0.0
    if skus_df is not None and not skus_df.empty:
        for _, row in skus_df.iterrows():
            sku = str(row["sku"])
            title = row["product_title"]
            qty = units.get(sku, 0.0)
            pts = int(lookup.get(title, row.get("current_points") or 0) or 0)
            issued = qty * pts
            if qty <= 0:
                continue
            rows.append({
                "sku": sku,
                "product_title": title,
                "units": qty,
                "points_per_unit": pts,
                "points_issued": issued,
            })
            total_points += issued
            total_units += qty
    rows.sort(key=lambda r: r["points_issued"], reverse=True)
    return {
        "skus": rows,
        "total_units": total_units,
        "total_points_issued": total_points,
    }


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
    try:
        if name.endswith(".csv"):
            df = pd.read_csv(buffer)
        elif name.endswith((".xlsx", ".xls")):
            df = pd.read_excel(buffer)
        else:
            return None, "Unsupported file type. Please upload a CSV or Excel file."
    except Exception as exc:
        return None, f"Could not read file: {exc}"

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
