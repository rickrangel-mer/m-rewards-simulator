"""Launch forecast: points to market by behavior, redemptions and payout.

The forecast starts from how a brand's stores bought in its last complete
months and runs month by month for a chosen horizon.

Points come from four behaviors, measured from the brand's own orders:

* regulars: stores that ordered in the last 3 months, on SKUs they already buy
* new items: those same stores ordering a SKU they did not buy in that window
* returning: stores with older orders but none in the last 3 months
* new stores: stores ordering the brand for the first time

Each behavior's normal monthly rate comes from the average of the last three
complete months. Launch-lift sliders raise or lower those rates. Their
defaults come from a measured launch (see ``benchmarks/``), counted only when
the launch month fell outside the normal month-to-month range.

Points carry over month to month in a wallet. Redeeming is optional: each
month a share of the stores that can afford the cheapest reward redeem. A
redeeming store claims every reward it can afford, cheapest first, and each
claim spends that reward's points. Whatever is left stays in the market.

Store choices (who tries an item, who redeems) use a seeded hash of the store
and month, so the same inputs give the same forecast, and raising a rate only
ever adds stores.
"""

from __future__ import annotations

import json
import zlib
from pathlib import Path

import pandas as pd

from simulator import format_usd, normalize_reward

BENCHMARK_PATH = Path(__file__).parent / "benchmarks" / "red_bull_2026_10.json"

BEHAVIORS = (
    ("regulars", "Regulars buying their usual lines",
     "Stores that ordered in the last 3 months, on SKUs they already buy"),
    ("new_items", "Regulars trying new items",
     "The same stores ordering a SKU they did not buy in the last 3 months"),
    ("returning", "Returning stores",
     "Stores with older orders of this brand but none in the last 3 months"),
    ("new_stores", "New stores",
     "Stores ordering this brand for the first time"),
)
BEHAVIOR_KEYS = tuple(key for key, _label, _desc in BEHAVIORS)

# Slider key -> (label, the profile rate it scales).
LIFTS = (
    ("regulars", "Regulars buying more", "keep_rate"),
    ("new_items", "Stores trying new items", "trial_rate"),
    ("returning", "Lapsed stores returning", "return_rate"),
    ("new_stores", "New stores per month", "new_store_rate"),
    ("new_store_basket", "New stores' first order size", "new_store_points"),
)
LIFT_KEYS = tuple(key for key, _label, _rate in LIFTS)

HORIZONS = (3, 6, 12)
DEFAULT_HORIZON = 6
DEFAULT_REDEEM_PCT = 10.0
WINDOW = 3


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def _clamp_number(value, default: float, lo: float, hi: float) -> float:
    text = str(value if value is not None else "").strip().rstrip("%")
    if not text:
        return default
    try:
        number = float(text)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return max(lo, min(hi, number))


def load_benchmark(path: Path | None = None) -> dict | None:
    """The measured launch whose lifts and redemption rate seed the sliders."""
    path = path or BENCHMARK_PATH
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def default_settings(benchmark: dict | None = None) -> dict:
    lifts = {key: 0.0 for key in LIFT_KEYS}
    redeem = DEFAULT_REDEEM_PCT
    if benchmark:
        for key in LIFT_KEYS:
            lifts[key] = float((benchmark.get("lifts") or {}).get(key, {}).get("default_pct", 0) or 0)
        redeem = float(benchmark.get("redeem_pct", DEFAULT_REDEEM_PCT) or DEFAULT_REDEEM_PCT)
    return {"lifts": lifts, "redeem_pct": redeem, "horizon": DEFAULT_HORIZON}


def parse_settings(params, benchmark: dict | None = None) -> dict:
    """Slider values from query parameters, falling back to the benchmark defaults."""
    params = params or {}
    defaults = default_settings(benchmark)
    lifts = {
        key: round(_clamp_number(params.get(f"fc_{key}"), defaults["lifts"][key], -100, 300))
        for key in LIFT_KEYS
    }
    redeem = round(_clamp_number(params.get("fc_redeem"), defaults["redeem_pct"], 0, 100), 1)
    horizon = int(_clamp_number(params.get("fc_months"), defaults["horizon"], 1, 24))
    if horizon not in HORIZONS:
        horizon = DEFAULT_HORIZON
    return {"lifts": lifts, "redeem_pct": redeem, "horizon": horizon}


def settings_query(settings: dict, benchmark: dict | None = None) -> list[tuple[str, str]]:
    """Query pairs for settings that differ from the defaults, to keep links short."""
    defaults = default_settings(benchmark)
    pairs = []
    for key in LIFT_KEYS:
        if round(settings["lifts"][key]) != round(defaults["lifts"][key]):
            pairs.append((f"fc_{key}", str(int(round(settings["lifts"][key])))))
    if settings["redeem_pct"] != round(defaults["redeem_pct"], 1):
        pairs.append(("fc_redeem", f"{settings['redeem_pct']:g}"))
    if settings["horizon"] != defaults["horizon"]:
        pairs.append(("fc_months", str(settings["horizon"])))
    return pairs


# ---------------------------------------------------------------------------
# Measuring behavior from orders
# ---------------------------------------------------------------------------

def monthly_points(raw: pd.DataFrame, sku_points: dict, last_month=None) -> pd.DataFrame:
    """Store x SKU x calendar month points, for SKUs that earn points."""
    cols = ["store_id", "sku", "month", "units", "points"]
    if raw is None or raw.empty or not sku_points:
        return pd.DataFrame(columns=cols)
    df = raw[raw["sku"].astype(str).isin(sku_points)].copy()
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["sku"] = df["sku"].astype(str)
    df["store_id"] = df["store_id"].astype(str)
    df["month"] = pd.to_datetime(df["order_date"]).dt.to_period("M")
    if last_month is not None:
        df = df[df["month"] <= pd.Period(last_month, "M")]
    out = df.groupby(["store_id", "sku", "month"], as_index=False)["total_quantity"].sum()
    out = out.rename(columns={"total_quantity": "units"})
    out["units"] = pd.to_numeric(out["units"], errors="coerce").fillna(0).astype(float)
    out["points"] = out["units"] * out["sku"].map(sku_points).astype(float)
    return out[cols]


def _ratio(num: float, den: float) -> float:
    return float(num) / float(den) if den else 0.0


def segment_month(monthly: pd.DataFrame, month) -> pd.DataFrame | None:
    """One month's store x SKU rows, each tagged with the behavior behind it.

    Compared with the three months before ``month``: a SKU the store bought
    then is ``regulars``; another SKU from a store active then is
    ``new_items``; a store with only older orders is ``returning``; a store
    with no earlier orders is ``new_stores``. Returns None when the data does
    not reach back far enough to fill that window.
    """
    if monthly is None or monthly.empty:
        return None
    month = pd.Period(month, "M")
    window = [month - k for k in range(WINDOW, 0, -1)]
    if monthly["month"].min() > window[0]:
        return None
    win = monthly[monthly["month"].isin(window)]
    cur = monthly[monthly["month"] == month]
    win_stores = set(win["store_id"])
    win_pairs = set(zip(win["store_id"], win["sku"]))
    older_stores = set(monthly[monthly["month"] < window[0]]["store_id"])
    segment = [
        "regulars" if (s, k) in win_pairs
        else "new_items" if s in win_stores
        else "returning" if s in older_stores
        else "new_stores"
        for s, k in zip(cur["store_id"], cur["sku"])
    ]
    return cur.assign(segment=segment)


def behavior_profile(monthly: pd.DataFrame, month) -> dict | None:
    """Split one month's points into the four behaviors and measure each rate."""
    cur = segment_month(monthly, month)
    if cur is None:
        return None
    month = pd.Period(month, "M")
    window = [month - k for k in range(WINDOW, 0, -1)]
    win = monthly[monthly["month"].isin(window)]
    win_stores = set(win["store_id"])
    lapsed = set(monthly[monthly["month"] < window[0]]["store_id"]) - win_stores
    points = cur.groupby("segment")["points"].sum()
    per_store = {key: cur[cur["segment"] == key].groupby("store_id")["points"].sum() for key in BEHAVIOR_KEYS}

    base_points = float(win["points"].sum()) / WINDOW
    active = float(win.groupby("month")["store_id"].nunique().mean()) if not win.empty else 0.0
    components = {key: float(points.get(key, 0.0)) for key in BEHAVIOR_KEYS}
    return {
        "month": str(month),
        "total": float(cur["points"].sum()),
        "components": components,
        "base_points": base_points,
        "window_stores": len(win_stores),
        "active_stores": active,
        "lapsed_pool": len(lapsed),
        "keep_rate": _ratio(components["regulars"], base_points),
        "trial_rate": _ratio(len(per_store["new_items"]), len(win_stores)),
        "trial_points": float(per_store["new_items"].mean()) if len(per_store["new_items"]) else 0.0,
        "return_rate": _ratio(len(per_store["returning"]), len(lapsed)),
        "return_points": float(per_store["returning"].mean()) if len(per_store["returning"]) else 0.0,
        "new_store_rate": _ratio(len(per_store["new_stores"]), active),
        "new_store_points": float(per_store["new_stores"].mean()) if len(per_store["new_stores"]) else 0.0,
    }


RATE_FIELDS = (
    "keep_rate", "trial_rate", "trial_points", "return_rate", "return_points",
    "new_store_rate", "new_store_points",
)


def steady_profile(monthly: pd.DataFrame, end_month, n: int = 3) -> dict | None:
    """Average behavior over the last ``n`` complete months that have a full window."""
    end_month = pd.Period(end_month, "M")
    profiles = [p for p in (behavior_profile(monthly, end_month - k) for k in range(n)) if p]
    if not profiles:
        return None
    out = {field: sum(p[field] for p in profiles) / len(profiles) for field in RATE_FIELDS}
    out["months"] = sorted(p["month"] for p in profiles)
    out["components"] = {
        key: sum(p["components"][key] for p in profiles) / len(profiles) for key in BEHAVIOR_KEYS
    }
    out["total"] = sum(p["total"] for p in profiles) / len(profiles)
    return out


def launch_effect(monthly: pd.DataFrame, launch_month, n_normal: int = 3) -> dict:
    """Compare a launch month's rates with the months just before it.

    A lift counts toward the slider default only when the launch month sits
    outside the range of those normal months; otherwise it is noise.
    """
    launch_month = pd.Period(launch_month, "M")
    launch = behavior_profile(monthly, launch_month)
    normal = [p for p in (behavior_profile(monthly, launch_month - k) for k in range(1, n_normal + 1)) if p]
    if not launch or not normal:
        return {}
    out = {}
    for key, _label, field in LIFTS:
        values = [p[field] for p in normal]
        mean = sum(values) / len(values)
        measured = (launch[field] / mean - 1) * 100 if mean else 0.0
        outside = not (min(values) <= launch[field] <= max(values))
        out[key] = {
            "field": field,
            "launch": launch[field],
            "normal_mean": mean,
            "normal_min": min(values),
            "normal_max": max(values),
            "measured_pct": round(measured, 1),
            "outside_normal": outside,
            "default_pct": round(measured) if outside else 0,
        }
    return {
        "launch": launch,
        "normal_months": [p["month"] for p in normal],
        "lifts": out,
    }


# ---------------------------------------------------------------------------
# The forecast
# ---------------------------------------------------------------------------

def _draw(*parts) -> float:
    """Deterministic uniform draw in [0, 1) for a store/month/purpose key."""
    key = "|".join(str(p) for p in parts).encode()
    return zlib.crc32(key) / 2**32


def _rewards_by_cost(rewards) -> list[tuple[str, int, int]]:
    items = [normalize_reward(r) for r in rewards or []]
    return sorted((r for r in items if r[1] > 0), key=lambda r: (r[1], r[2], r[0]))


def _keep(store, m, tag, factor) -> bool:
    """Keep an existing event when a negative lift removes ``1 - factor`` of them."""
    return factor >= 1 or _draw("keep", tag, store, m) < factor


def _extra_rate(rate: float, factor: float) -> float:
    """Chance a store that did not act this month does, so the total rate is rate × factor."""
    if factor <= 1 or rate >= 1:
        return 0.0
    return min(1.0, rate * (factor - 1) / (1 - rate))


def _run(replay, regulars, lapsed, profile, lifts, rewards, redeem_pct, horizon):
    """One pass of the month-by-month wallet. Returns per-month rows and claims.

    ``replay`` holds the last three months' points per store and behavior.
    Forecast month m replays window month (m - 1) mod 3, then applies lifts.
    """
    f = {key: 1 + lifts.get(key, 0) / 100 for key in LIFT_KEYS}
    p_redeem = max(0.0, min(1.0, redeem_pct / 100))
    min_cost = rewards[0][1] if rewards else None
    extra_trial = _extra_rate(profile["trial_rate"], f["new_items"])
    extra_return = _extra_rate(profile["return_rate"], f["returning"])

    balance: dict[str, float] = {}
    claims = {name: 0 for name, _pts, _cents in rewards}
    months = []
    stores_by_behavior = {key: 0 for key in BEHAVIOR_KEYS}
    for m in range(1, horizon + 1):
        events = replay[(m - 1) % len(replay)]
        issued = {key: 0.0 for key in BEHAVIOR_KEYS}
        touched = {key: set() for key in BEHAVIOR_KEYS}

        def earn(store, pts, key):
            if pts <= 0:
                return
            balance[store] = balance.get(store, 0.0) + pts
            issued[key] += pts
            touched[key].add(store)

        for (store, key), pts in events.items():
            if key == "regulars":
                earn(store, pts * f["regulars"], key)
            elif key == "new_items" and _keep(store, m, key, f["new_items"]):
                earn(store, pts, key)
            elif key == "returning" and _keep(store, m, key, f["returning"]):
                earn(store, pts, key)
            elif key == "new_stores" and _keep(store, m, key, f["new_stores"]):
                earn(store, pts * f["new_store_basket"], key)
        if extra_trial:
            for store in regulars:
                if (store, "new_items") not in events and _draw("trial", store, m) < extra_trial:
                    earn(store, profile["trial_points"], "new_items")
        if extra_return:
            for store in lapsed:
                if (store, "returning") not in events and _draw("return", store, m) < extra_return:
                    earn(store, profile["return_points"], "returning")
        if f["new_stores"] > 1:
            seen = sum(1 for (_s, key) in events if key == "new_stores")
            for k in range(round(seen * (f["new_stores"] - 1))):
                earn(f"new:{m}:{k}", profile["new_store_points"] * f["new_store_basket"], "new_stores")
        for key in BEHAVIOR_KEYS:
            stores_by_behavior[key] += len(touched[key])

        redeemed = 0.0
        payout = 0
        eligible = redeemers = 0
        if min_cost is not None:
            for store in list(balance):
                if balance[store] < min_cost:
                    continue
                eligible += 1
                if _draw("redeem", store, m) >= p_redeem:
                    continue
                redeemers += 1
                for name, cost, cents in rewards:
                    if balance[store] >= cost:
                        balance[store] -= cost
                        redeemed += cost
                        payout += cents
                        claims[name] += 1
        months.append({
            "month": m,
            "issued": issued,
            "issued_total": sum(issued.values()),
            "redeemed": redeemed,
            "payout_cents": payout,
            "eligible": eligible,
            "redeemers": redeemers,
            "outstanding": sum(balance.values()),
        })
    return {
        "months": months,
        "claims": claims,
        "stores_by_behavior": {k: v / horizon for k, v in stores_by_behavior.items()},
    }


def _span_label(first: pd.Period, last: pd.Period) -> str:
    if first.year == last.year:
        return f"{first.strftime('%B')}–{last.strftime('%B %Y')}"
    return f"{first.strftime('%B %Y')}–{last.strftime('%B %Y')}"


def forecast_launch(
    monthly: pd.DataFrame,
    end_month,
    rewards,
    settings: dict,
) -> dict | None:
    """Forecast points issued, redeemed and paid out over ``settings['horizon']`` months.

    ``end_month`` is the last complete month of orders. Returns None when there
    is not enough history to measure a normal month.
    """
    if monthly is None or monthly.empty or end_month is None:
        return None
    end_month = pd.Period(end_month, "M")
    profile = steady_profile(monthly, end_month)
    if profile is None:
        return None

    window = [end_month - k for k in range(WINDOW - 1, -1, -1)]
    replay = []
    for month in window:
        seg = segment_month(monthly, month)
        if seg is None:
            return None
        replay.append(seg.groupby(["store_id", "segment"])["points"].sum().to_dict())
    win = monthly[monthly["month"].isin(window)]
    regulars = sorted(set(win["store_id"]))
    lapsed = sorted(set(monthly[monthly["month"] < window[0]]["store_id"]) - set(regulars))
    rewards_sorted = _rewards_by_cost(rewards)
    horizon = int(settings.get("horizon") or DEFAULT_HORIZON)
    redeem_pct = float(settings.get("redeem_pct", DEFAULT_REDEEM_PCT))
    lifts = settings.get("lifts") or {}

    run = _run(replay, regulars, lapsed, profile, lifts, rewards_sorted, redeem_pct, horizon)
    flat = _run(replay, regulars, lapsed, profile, {}, rewards_sorted, redeem_pct, horizon)

    issued = sum(row["issued_total"] for row in run["months"])
    redeemed = sum(row["redeemed"] for row in run["months"])
    payout = sum(row["payout_cents"] for row in run["months"])
    outstanding = run["months"][-1]["outstanding"] if run["months"] else 0.0
    flat_issued = sum(row["issued_total"] for row in flat["months"])
    flat_payout = sum(row["payout_cents"] for row in flat["months"])

    total_cost = sum(pts for _n, pts, _c in rewards_sorted)
    total_cents = sum(c for _n, _p, c in rewards_sorted)
    if redeemed > 0 and payout > 0:
        cents_per_point = payout / redeemed
    else:
        cents_per_point = total_cents / total_cost if total_cost else 0.0
    outstanding_cents = round(outstanding * cents_per_point)

    behaviors = []
    for key, label, desc in BEHAVIORS:
        pts = sum(row["issued"][key] for row in run["months"])
        base_pts = sum(row["issued"][key] for row in flat["months"])
        behaviors.append({
            "key": key,
            "label": label,
            "desc": desc,
            "stores_per_month": run["stores_by_behavior"][key],
            "points": pts,
            "share": _ratio(pts, issued) * 100,
            "lift_points": pts - base_pts,
        })

    reward_rows = []
    for name, cost, cents in rewards_sorted:
        count = run["claims"][name]
        reward_rows.append({
            "name": name,
            "points": cost,
            "value_cents": cents,
            "value_label": format_usd(cents),
            "claims": count,
            "points_spent": count * cost,
            "cost_cents": count * cents,
            "cost_label": format_usd(count * cents),
        })

    month_rows = []
    for row in run["months"]:
        month_rows.append({
            **row,
            "label": f"Month {row['month']}",
            "payout_label": format_usd(row["payout_cents"]),
        })

    return {
        "horizon": horizon,
        "start_month": str(end_month + 1),
        "start_label": (end_month + 1).strftime("%B %Y"),
        "end_label": (end_month + horizon).strftime("%B %Y"),
        "base_months": profile["months"],
        "base_label": _span_label(window[0], window[-1]),
        "profile": profile,
        "settings": settings,
        "issued": issued,
        "redeemed": redeemed,
        "payout_cents": payout,
        "payout_label": format_usd(payout),
        "outstanding": outstanding,
        "outstanding_cents": outstanding_cents,
        "outstanding_label": format_usd(outstanding_cents),
        "cents_per_point": cents_per_point,
        "redeemed_share": _ratio(redeemed, issued) * 100,
        "without_lift": {
            "issued": flat_issued,
            "payout_cents": flat_payout,
            "payout_label": format_usd(flat_payout),
        },
        "behaviors": behaviors,
        "rewards": reward_rows,
        "months": month_rows,
        "no_rewards": not rewards_sorted,
    }
