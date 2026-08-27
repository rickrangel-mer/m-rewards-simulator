"""Monthly Railway cron: pull the previous complete month from Athena into Postgres.

On first run (empty refresh_state) backfills the last 6 complete months.
Set REFRESH_BACKFILL=1 to force a full backfill.
"""

from __future__ import annotations

import os
from datetime import date

from data import (
    NUM_MONTHS,
    backfill_windows,
    count_orders,
    fetch_order_data,
    get_connection,
    get_refresh_state,
    init_schema,
    load_all_skus,
    load_catalog_skus,
    previous_month_window,
    replace_month_orders,
    replace_sku_orders,
    set_refresh_state,
)


def refresh_windows(today: date, state: dict | None, num_months: int = NUM_MONTHS) -> list[tuple[date, date]]:
    force_backfill = os.environ.get("REFRESH_BACKFILL", "").strip() in ("1", "true", "yes")
    if force_backfill or state is None or not state.get("last_refreshed_month"):
        return backfill_windows(today, num_months=num_months)
    return [previous_month_window(today)]


def run_refresh(
    today: date | None = None,
    sku_list: list[str] | None = None,
    fetch_fn=None,
    conn=None,
    num_months: int = NUM_MONTHS,
) -> dict:
    today = today or date.today()
    fetch_fn = fetch_fn or fetch_order_data
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    try:
        init_schema(conn)
        if sku_list is None:
            sku_list = load_all_skus(conn=conn)

        state = get_refresh_state(conn)
        windows = refresh_windows(today, state, num_months=num_months)
        if not windows:
            raise RuntimeError("No refresh windows computed")

        for start, end in windows:
            print(f"Fetching Athena orders {start.isoformat()} to {end.isoformat()} (exclusive)...")
            orders = fetch_fn(sku_list, start, end)
            n = replace_month_orders(conn, orders, start, end)
            print(f"  upserted {n:,} rows")

        last_start, _last_end = windows[-1]
        month_key = last_start.strftime("%Y-%m")
        row_count = count_orders(conn)
        set_refresh_state(conn, month_key, row_count)
        print(f"Refresh complete. Order data through {month_key} ({row_count:,} rows).")
        return {
            "last_refreshed_month": month_key,
            "row_count": row_count,
            "windows": windows,
        }
    finally:
        if close_conn:
            conn.close()


def run_brand_refresh(
    brand: str,
    today: date | None = None,
    sku_list: list[str] | None = None,
    fetch_fn=None,
    conn=None,
    num_months: int = NUM_MONTHS,
) -> dict:
    """Pull Athena orders for one brand's SKUs over the last complete months.

    Does not replace other brands' rows and does not update refresh_state
    (the monthly cron still owns the full snapshot).
    """
    today = today or date.today()
    fetch_fn = fetch_fn or fetch_order_data
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    try:
        init_schema(conn)
        if sku_list is None:
            sku_list = [str(row["sku"]) for row in load_catalog_skus(brand, conn=conn)]
        sku_list = [str(s) for s in sku_list if str(s).strip()]
        if not sku_list:
            raise ValueError("No participating SKUs on this brand.")

        windows = backfill_windows(today, num_months=num_months)
        if not windows:
            raise RuntimeError("No refresh windows computed")
        start, end = windows[0][0], windows[-1][1]
        print(
            f"Fetching Athena orders for {brand} "
            f"{start.isoformat()} to {end.isoformat()} (exclusive), {len(sku_list)} SKUs..."
        )
        orders = fetch_fn(sku_list, start, end)
        n = replace_sku_orders(conn, orders, start, end, sku_list)
        print(f"  upserted {n:,} rows for {brand}")
        return {
            "brand": brand,
            "rows": n,
            "sku_count": len(sku_list),
            "start": start,
            "end": end,
            "windows": windows,
        }
    finally:
        if close_conn:
            conn.close()


def main() -> None:
    run_refresh()


if __name__ == "__main__":
    main()
