"""Run the simulator on your machine without Postgres.

Two steps:

    python demo.py fetch --brand-name "Red Bull"     # Athena -> demo_data/red-bull/
    python demo.py serve                              # http://localhost:8080

``fetch`` reads Athena only (orders, the points ledger, prize redemptions) and
writes plain files under ``demo_data/<slug>/``. ``serve`` runs the normal web
app with every database call swapped for those files. Nothing connects to
Postgres: the database variables are removed from the process and the
connection function raises if anything still tries.

Everything you change in demo mode (proposed points, rewards, catalog uploads,
Pull order history) is saved back to ``demo_data/`` and nowhere else. Delete a
brand's folder to start over.

``demo_data/`` holds store-level order rows and is git-ignored. Keep it local.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import ExitStack
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

DEMO_DIR = Path(__file__).parent / "demo_data"
DB_ENV_VARS = (
    "DATABASE_URL", "DATABASE_PRIVATE_URL", "DATABASE_PUBLIC_URL",
    "PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE",
)
DEMO_USER = {"id": 1, "email": "demo@localhost", "role": "operator", "brands": []}


class DemoModeError(RuntimeError):
    pass


def _no_postgres(*_args, **_kwargs):
    raise DemoModeError("Demo mode does not connect to Postgres.")


def strip_database_env(environ=os.environ) -> None:
    for key in DB_ENV_VARS:
        environ.pop(key, None)


# ---------------------------------------------------------------------------
# Fetch: Athena -> demo_data/<slug>/
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")


def month_window(months: int, today: date | None = None) -> tuple[date, date]:
    """[start, end) covering the last ``months`` complete calendar months."""
    today = today or date.today()
    end = today.replace(day=1)
    start = (pd.Period(end, "M") - months).start_time.date()
    return start, end


def fetch_rewards(conn, vendor_name: str) -> list[tuple[str, int, int]]:
    """Prizes and their point cost and dollar cost, from real redemptions."""
    from data import _sql_quote

    table = "dw.ads_m_rewards_portal_vendor_prize_redeem_daily"
    query = f"""
    SELECT prize_name,
           SUM(points_spent) / NULLIF(SUM(redeemed_prize_cnt), 0) AS points,
           SUM(COALESCE(cost, 0)) / NULLIF(SUM(redeemed_prize_cnt), 0) AS dollars,
           MAX(redeem_date) AS last_redeemed
    FROM {table}
    WHERE dt = (SELECT MAX(dt) FROM {table}) AND vendor_name = {_sql_quote(vendor_name)}
    GROUP BY 1
    """
    df = pd.read_sql(query, conn)
    rows = []
    for r in df.sort_values("points").itertuples():
        if r.points and r.points > 0:
            rows.append((str(r.prize_name), int(round(float(r.points))), int(round(float(r.dollars or 0) * 100))))
    return rows


def fetch_titles(conn, brand_name: str, start: date, end: date) -> dict[str, str]:
    from data import _sql_quote

    table = "dwm.dwm_trade_line_item_detail_full"
    query = f"""
    SELECT sku, MAX(title) AS title FROM {table}
    WHERE dt = (SELECT MAX(dt) FROM {table}) AND brand_name = {_sql_quote(brand_name)}
      AND order_item_created_at >= TIMESTAMP '{start.isoformat()} 00:00:00'
      AND order_item_created_at <  TIMESTAMP '{end.isoformat()} 00:00:00'
    GROUP BY 1
    """
    df = pd.read_sql(query, conn)
    return {str(r.sku): str(r.title or "") for r in df.itertuples()}


def fetch_orders(conn, brand_name: str, start: date, end: date) -> pd.DataFrame:
    from calibrate_launch import fetch_brand_orders

    return fetch_brand_orders(conn, brand_name, start, end)


def fetch_brand(
    brand_name: str,
    slug: str | None = None,
    label: str | None = None,
    vendor_name: str | None = None,
    months: int = 12,
    demo_dir: Path = DEMO_DIR,
    conn=None,
    today: date | None = None,
) -> Path:
    """Pull one brand from Athena into ``demo_dir/<slug>/``."""
    from calibrate_launch import find_partner, infer_sku_points
    from data import _athena_connect

    slug = slug or slugify(brand_name)
    conn = conn or _athena_connect()
    start, end = month_window(months, today)
    last_month = pd.Period(end, "M") - 1

    print(f"Orders for {brand_name}, {start:%b %Y}–{last_month.strftime('%b %Y')} ...", flush=True)
    orders = fetch_orders(conn, brand_name, start, end)
    titles = fetch_titles(conn, brand_name, start, end)

    print("Points per SKU from the points ledger ...", flush=True)
    try:
        partner = find_partner(conn, brand_name, last_month)
        sku_points = infer_sku_points(conn, brand_name, partner, last_month)
    except SystemExit:
        sku_points = {}
    if sku_points:
        skus = sorted(sku_points)
    else:
        print("  No M-Rewards points found for this brand: every SKU starts at 0 points.", flush=True)
        skus = sorted(set(orders["sku"].astype(str)))
    catalog = [
        {"sku": sku, "product_title": titles.get(sku, ""), "current_points": int(round(sku_points.get(sku, 0)))}
        for sku in skus
    ]

    print("Rewards from past redemptions ...", flush=True)
    rewards = fetch_rewards(conn, vendor_name or brand_name)
    if not rewards:
        print("  No redemptions found: add rewards on the brand page.", flush=True)

    store = DemoStore(demo_dir)
    folder = store.folder(slug)
    folder.mkdir(parents=True, exist_ok=True)
    orders = orders[orders["sku"].astype(str).isin(skus)]
    store.write_orders(slug, orders)
    store.write_json(slug, "catalog.json", catalog)
    if not (folder / "rewards.json").exists():
        store.write_json(slug, "rewards.json", [list(r) for r in rewards])
    store.write_json(slug, "brand.json", {
        "label": label or brand_name,
        "brand_name": brand_name,
        "vendor_name": vendor_name or brand_name,
        "months": months,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    })
    print(f"Wrote {len(orders):,} order rows, {len(catalog)} SKUs, {len(rewards)} rewards to {folder}", flush=True)
    return folder


# ---------------------------------------------------------------------------
# Serve: the web app on local files
# ---------------------------------------------------------------------------

class DemoStore:
    """File-backed stand-in for the Postgres tables the web app reads and writes."""

    def __init__(self, demo_dir: Path = DEMO_DIR):
        self.dir = Path(demo_dir)
        self._orders_cache: tuple | None = None

    # files
    def folder(self, slug: str) -> Path:
        return self.dir / slug

    def read_json(self, slug: str, name: str, default):
        path = self.folder(slug) / name
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return default

    def write_json(self, slug: str, name: str, value) -> None:
        folder = self.folder(slug)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(json.dumps(value, indent=2) + "\n")

    def write_orders(self, slug: str, orders: pd.DataFrame) -> None:
        folder = self.folder(slug)
        folder.mkdir(parents=True, exist_ok=True)
        cols = ["store_id", "sku", "order_date", "total_quantity"]
        orders[cols].to_csv(folder / "orders.csv", index=False)
        self._orders_cache = None

    def slugs(self) -> list[str]:
        if not self.dir.exists():
            return []
        return sorted(p.name for p in self.dir.iterdir() if (p / "brand.json").exists())

    # orders
    def load_orders_or_error(self):
        files = [self.folder(s) / "orders.csv" for s in self.slugs()]
        files = [f for f in files if f.exists()]
        stamp = tuple((str(f), f.stat().st_mtime) for f in files)
        if not self._orders_cache or self._orders_cache[0] != stamp:
            frames = [pd.read_csv(f, dtype={"store_id": str, "sku": str}) for f in files]
            raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            if not raw.empty:
                raw["order_date"] = pd.to_datetime(raw["order_date"])
                raw["total_quantity"] = pd.to_numeric(raw["total_quantity"], errors="coerce").fillna(0)
            self._orders_cache = (stamp, raw)
        raw = self._orders_cache[1]
        if raw is None or raw.empty:
            return None, None, "No demo orders yet. Run: python demo.py fetch --brand-name \"<Athena brand name>\""
        last = raw["order_date"].max().strftime("%Y-%m")
        return raw, {"last_refreshed_month": last}, None

    # brands
    def _brand_row(self, slug: str, sort: int) -> dict:
        from data import PALETTE_THEMES

        meta = self.read_json(slug, "brand.json", {})
        theme = meta.get("theme") or (slug if slug in PALETTE_THEMES else "default")
        return {"slug": slug, "label": meta.get("label") or slug, "theme": theme, "sort": sort}

    def load_brands(self, conn=None) -> list[dict]:
        return [self._brand_row(slug, i) for i, slug in enumerate(self.slugs())]

    def get_brand(self, slug: str, conn=None) -> dict | None:
        return next((row for row in self.load_brands() if row["slug"] == slug), None)

    def create_brand(self, slug, label, theme, records, rewards, conn=None) -> dict:
        from data import DuplicateBrandError

        if self.get_brand(slug):
            raise DuplicateBrandError(slug)
        self.write_json(slug, "brand.json", {"label": label, "theme": theme})
        self.replace_catalog_skus(slug, records)
        self.save_brand_rewards(slug, rewards)
        return self.get_brand(slug)

    # catalog, points, rewards
    def load_catalog_skus(self, brand: str, conn=None) -> list[dict]:
        return [dict(r) for r in self.read_json(brand, "catalog.json", [])]

    def replace_catalog_skus(self, brand: str, records: list[dict], conn=None) -> int:
        from data import overlay_catalog

        normalized = overlay_catalog([], records)
        self.write_json(brand, "catalog.json", normalized)
        keep = {r["sku"] for r in normalized}
        proposed = self.load_proposed_points(brand)
        self.write_json(brand, "proposed.json", {k: v for k, v in proposed.items() if k in keep})
        return len(normalized)

    def merge_catalog_skus(self, brand: str, records: list[dict], conn=None) -> list[dict]:
        from data import overlay_catalog

        merged = overlay_catalog(self.load_catalog_skus(brand), records)
        self.replace_catalog_skus(brand, merged)
        return [dict(r) for r in merged]

    def load_proposed_points(self, brand: str, conn=None) -> dict[str, int]:
        return {str(k): int(v) for k, v in self.read_json(brand, "proposed.json", {}).items()}

    def merge_proposed_points(self, brand: str, patch_: dict[str, int], conn=None) -> dict[str, int]:
        from data import overlay_proposed_points

        merged = overlay_proposed_points(self.load_proposed_points(brand), patch_)
        self.write_json(brand, "proposed.json", merged)
        return dict(merged)

    def load_brand_rewards(self, brand: str, conn=None) -> list[tuple[str, int, int]]:
        rows = self.read_json(brand, "rewards.json", [])
        return [(str(r[0]), int(r[1]), int(r[2]) if len(r) > 2 else 0) for r in rows]

    def save_brand_rewards(self, brand: str, rewards: list[tuple], conn=None, commit: bool = True) -> None:
        rows = [[str(r[0]), int(r[1]), int(r[2]) if len(r) > 2 else 0] for r in rewards]
        self.write_json(brand, "rewards.json", rows)

    # Pull order history: refresh this brand's files from Athena.
    def run_brand_refresh(self, brand: str, sku_list=None, **_kwargs) -> dict:
        meta = self.read_json(brand, "brand.json", {})
        if not meta.get("brand_name"):
            raise DemoModeError("This demo brand was not fetched from Athena, so there is nothing to pull.")
        from data import _athena_connect

        months = int(meta.get("months") or 12)
        start, end = month_window(months)
        orders = fetch_orders(_athena_connect(), meta["brand_name"], start, end)
        skus = {str(s) for s in (sku_list or [])}
        if skus:
            orders = orders[orders["sku"].astype(str).isin(skus)]
        self.write_orders(brand, orders)
        last = (pd.Period(end, "M") - 1).start_time.date()
        return {"rows": len(orders), "sku_count": len(skus), "start": start, "windows": [(last, end)]}


def install(store: DemoStore) -> ExitStack:
    """Point the web app at ``store`` and block Postgres. Close the stack to undo."""
    strip_database_env()
    import app as webapp
    import data

    stack = ExitStack()
    for target, name in ((data, "get_database_url"), (data, "get_connection"), (webapp, "get_connection")):
        stack.enter_context(patch.object(target, name, _no_postgres))
    stack.enter_context(patch.object(webapp, "ensure_schema", lambda: False))
    stack.enter_context(patch.object(webapp, "load_orders_or_error", store.load_orders_or_error))
    stack.enter_context(patch.object(webapp, "current_user", lambda request: dict(DEMO_USER)))
    stack.enter_context(patch.object(webapp, "get_user_by_id", lambda uid, conn=None: dict(DEMO_USER)))
    stack.enter_context(patch.object(webapp, "list_users", lambda conn=None: [dict(DEMO_USER, created_at=None)]))
    stack.enter_context(patch.object(webapp, "count_users", lambda conn=None: 1))
    stack.enter_context(patch.object(webapp, "run_brand_refresh", store.run_brand_refresh))
    for name in (
        "load_brands", "get_brand", "create_brand",
        "load_catalog_skus", "replace_catalog_skus", "merge_catalog_skus",
        "load_proposed_points", "merge_proposed_points",
        "load_brand_rewards", "save_brand_rewards",
    ):
        stack.enter_context(patch.object(webapp, name, getattr(store, name)))
    return stack


def serve(port: int = 8080, demo_dir: Path = DEMO_DIR) -> None:
    import uvicorn

    os.environ.setdefault("SESSION_SECRET", "demo-mode-local-only")
    store = DemoStore(demo_dir)
    install(store)  # stays installed for the life of the process
    import app as webapp

    brands = store.slugs()
    if not brands:
        print('No demo brands yet. Run: python demo.py fetch --brand-name "<Athena brand name>"')
    else:
        print(f"Demo brands: {', '.join(brands)}")
    print(f"Demo mode: no Postgres. Open http://localhost:{port}/brands/{brands[0] if brands else ''}")
    uvicorn.run(webapp.app, host="127.0.0.1", port=port)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Run the simulator locally without Postgres.")
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("fetch", help="Pull one brand from Athena into demo_data/")
    f.add_argument("--brand-name", required=True, help="Athena brand_name, e.g. 'Red Bull' or 'Monster'")
    f.add_argument("--slug", help="URL name (default: from the brand name)")
    f.add_argument("--label", help="Name shown in the app (default: the brand name)")
    f.add_argument("--vendor-name", help="M-Rewards vendor name for rewards, if it differs from the brand name")
    f.add_argument("--months", type=int, default=12, help="Complete months of orders to pull (default 12)")
    s = sub.add_parser("serve", help="Run the web app on demo_data/")
    s.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    if args.command == "fetch":
        strip_database_env()
        fetch_brand(args.brand_name, slug=args.slug, label=args.label,
                    vendor_name=args.vendor_name, months=args.months)
    else:
        serve(port=args.port)


if __name__ == "__main__":
    sys.exit(main())
