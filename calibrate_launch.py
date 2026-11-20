"""Measure a past M-Rewards launch and write the benchmark that seeds the forecast sliders.

Reads Athena directly (orders, the points ledger), so it runs where the cron
service's AWS credentials are available. It does not touch Postgres.

    python calibrate_launch.py --brand-name "Red Bull" --launch 2026-10 \\
        --out benchmarks/red_bull_2026_10.json

What it measures:

* Points per SKU, read off the points ledger: for orders with a single SKU of
  the brand, points earned divided by units ordered.
* Each behavior's rate in the launch month against the three months before
  it (see forecast.launch_effect). A lift becomes a slider default only when
  the launch month sits outside the range of those months.
* How often stores that can afford the cheapest reward redeem in the launch
  month, from the ledger's running balances.
"""

from __future__ import annotations

import argparse
import json
from datetime import date

import pandas as pd

from data import _athena_connect, _sql_quote
from forecast import BEHAVIOR_KEYS, launch_effect, monthly_points

LINE_ITEMS = "dwm.dwm_trade_line_item_detail_full"
ORDERS = "dwm.dwm_trade_order_detail_full"
LEDGER = "ods.ods_backend_core_partner_points_ledger_full"


def _latest(table: str) -> str:
    return f"dt = (SELECT MAX(dt) FROM {table})"


def fetch_brand_orders(conn, brand_name: str, start: date, end: date) -> pd.DataFrame:
    """Store x SKU x day units for one brand, in the shape the app stores orders."""
    query = f"""
    SELECT o.store_id, li.sku, DATE(li.order_item_created_at) AS order_date,
           SUM(li.initial_quantity) AS total_quantity
    FROM {LINE_ITEMS} li
    JOIN {ORDERS} o ON li.order_id = o.order_id AND o.{_latest(ORDERS)}
    WHERE li.{_latest(LINE_ITEMS)}
      AND li.brand_name = {_sql_quote(brand_name)}
      AND li.order_item_created_at >= TIMESTAMP '{start.isoformat()} 00:00:00'
      AND li.order_item_created_at <  TIMESTAMP '{end.isoformat()} 00:00:00'
    GROUP BY 1, 2, 3
    """
    df = pd.read_sql(query, conn)
    df["store_id"] = df["store_id"].astype(str)
    df["sku"] = df["sku"].astype(str)
    df["order_date"] = pd.to_datetime(df["order_date"])
    return df


def find_partner(conn, brand_name: str, launch: pd.Period) -> str:
    """The ledger partner whose earning orders almost all contain this brand.

    An order often holds several brands, so counting overlaps alone picks the
    biggest program. The brand's own partner is the one where the highest
    share of earning orders include the brand.
    """
    start = launch.start_time.date().isoformat()
    end = (launch + 1).start_time.date().isoformat()
    query = f"""
    WITH buyers AS (
      SELECT DISTINCT li.order_id FROM {LINE_ITEMS} li
      WHERE li.{_latest(LINE_ITEMS)} AND li.brand_name = {_sql_quote(brand_name)}
        AND li.order_item_created_at >= TIMESTAMP '{start} 00:00:00'
        AND li.order_item_created_at <  TIMESTAMP '{end} 00:00:00'),
    earned AS (
      SELECT DISTINCT partner_id, order_id FROM {LEDGER}
      WHERE {_latest(LEDGER)} AND reason = 'ORDER_EARNED'
        AND created_at >= TIMESTAMP '{start} 00:00:00' AND created_at < TIMESTAMP '{end} 00:00:00')
    SELECT e.partner_id, COUNT(*) AS orders,
           1.0 * COUNT(b.order_id) / COUNT(*) AS brand_share
    FROM earned e LEFT JOIN buyers b ON e.order_id = b.order_id
    GROUP BY 1 HAVING COUNT(b.order_id) > 0
    ORDER BY brand_share DESC, orders DESC LIMIT 1
    """
    df = pd.read_sql(query, conn)
    if df.empty:
        raise SystemExit(f"No ledger partner earns points on {brand_name} orders in {launch}.")
    return str(df.iloc[0]["partner_id"])


def infer_sku_points(conn, brand_name: str, partner_id: str, launch: pd.Period) -> dict[str, float]:
    """Points per unit for each SKU, from single-SKU orders in the launch month."""
    start = launch.start_time.date().isoformat()
    end = (launch + 1).start_time.date().isoformat()
    query = f"""
    WITH led AS (
      SELECT order_id, SUM(points) pts FROM {LEDGER}
      WHERE {_latest(LEDGER)} AND partner_id = {_sql_quote(partner_id)}
        AND reason = 'ORDER_EARNED' AND status = 'CONFIRMED'
        AND created_at >= TIMESTAMP '{start} 00:00:00' AND created_at < TIMESTAMP '{end} 00:00:00'
      GROUP BY 1),
    li AS (
      SELECT order_id, sku, SUM(initial_quantity) q FROM {LINE_ITEMS}
      WHERE {_latest(LINE_ITEMS)} AND brand_name = {_sql_quote(brand_name)}
        AND order_item_created_at >= TIMESTAMP '{start} 00:00:00'
        AND order_item_created_at <  TIMESTAMP '{end} 00:00:00'
      GROUP BY 1, 2),
    one AS (SELECT order_id FROM li GROUP BY 1 HAVING COUNT(*) = 1)
    SELECT li.sku, approx_percentile(1.0 * led.pts / li.q, 0.5) AS pts
    FROM li JOIN one ON li.order_id = one.order_id JOIN led ON led.order_id = li.order_id
    WHERE li.q > 0
    GROUP BY 1
    """
    df = pd.read_sql(query, conn)
    return {str(r.sku): float(r.pts) for r in df.itertuples() if r.pts and r.pts > 0}


def ledger_redeem_rate(conn, partner_id: str, launch: pd.Period) -> dict:
    """Share of accounts able to afford the cheapest reward that redeemed in the launch month."""
    query = f"""
    SELECT user_account_id, date_trunc('month', created_at) AS month,
      SUM(CASE WHEN status = 'CONFIRMED' AND reason <> 'REDEEM' THEN points ELSE 0 END) AS earned,
      SUM(CASE WHEN status = 'CONFIRMED' AND reason = 'REDEEM' THEN points ELSE 0 END) AS redeemed,
      SUM(CASE WHEN status = 'CONFIRMED' AND reason = 'REDEEM' THEN 1 ELSE 0 END) AS events
    FROM {LEDGER}
    WHERE {_latest(LEDGER)} AND partner_id = {_sql_quote(partner_id)}
    GROUP BY 1, 2
    """
    df = pd.read_sql(query, conn)
    df["month"] = pd.to_datetime(df["month"]).dt.to_period("M")
    single = df[df["events"] == 1]
    min_cost = float((-single["redeemed"]).min()) if not single.empty else 0.0
    df = df.sort_values(["user_account_id", "month"])
    net = df["earned"] + df["redeemed"]
    df["before"] = net.groupby(df["user_account_id"]).cumsum() - net
    cur = df[df["month"] == launch]
    available = cur["before"] + cur["earned"]
    eligible = available >= min_cost if min_cost else available > 0
    redeemers = int((eligible & (cur["events"] > 0)).sum())
    return {
        "cheapest_reward_points": min_cost,
        "eligible_stores": int(eligible.sum()),
        "redeeming_stores": redeemers,
        "redeem_pct": round(redeemers / int(eligible.sum()) * 100, 1) if int(eligible.sum()) else 0.0,
    }


def build_benchmark(brand_name: str, launch: str, conn=None) -> dict:
    launch_p = pd.Period(launch, "M")
    conn = conn or _athena_connect()
    start = date(launch_p.year, 1, 1)
    end = (launch_p + 1).start_time.date()
    partner = find_partner(conn, brand_name, launch_p)
    sku_points = infer_sku_points(conn, brand_name, partner, launch_p)
    orders = fetch_brand_orders(conn, brand_name, start, end)
    effect = launch_effect(monthly_points(orders, sku_points), launch_p)
    if not effect:
        raise SystemExit("Not enough order history before the launch month to measure it.")
    redeem = ledger_redeem_rate(conn, partner, launch_p)
    launch_profile = effect["launch"]
    return {
        "label": f"{brand_name} launch, {launch_p.strftime('%B %Y')}",
        "brand_name": brand_name,
        "launch_month": str(launch_p),
        "normal_months": effect["normal_months"],
        "measured_on": date.today().isoformat(),
        "program_skus": len(sku_points),
        "launch_points": round(launch_profile["total"]),
        "launch_mix_pct": {
            key: round(launch_profile["components"][key] / launch_profile["total"] * 100, 1)
            for key in BEHAVIOR_KEYS
        } if launch_profile["total"] else {},
        "lifts": {
            key: {
                **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()},
            }
            for key, row in effect["lifts"].items()
        },
        **redeem,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--brand-name", required=True, help="Athena brand_name, e.g. 'Red Bull'")
    parser.add_argument("--launch", required=True, help="Launch month, YYYY-MM")
    parser.add_argument("--out", required=True, help="Where to write the benchmark JSON")
    args = parser.parse_args()
    benchmark = build_benchmark(args.brand_name, args.launch)
    with open(args.out, "w") as fh:
        json.dump(benchmark, fh, indent=2)
        fh.write("\n")
    print(json.dumps(benchmark, indent=2))


if __name__ == "__main__":
    main()
