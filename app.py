"""FastAPI + Jinja2 M-Rewards simulator (replaces Streamlit)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from data import (
    format_month_label,
    get_connection,
    get_refresh_state,
    load_cocacola_skus,
    load_ferrera_skus,
    load_monster_skus,
    read_orders,
)
from simulator import (
    BRAND_DEFAULTS,
    available_months,
    build_points_lookup,
    compute_store_penetration,
    get_month_orders,
    parse_imported_points,
    simulate,
    summarize_results,
)

BASE_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="M-Rewards Simulator")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "m-rewards-dev-secret-change-me"),
    max_age=60 * 60 * 12,
)


def _load_refresh_state():
    conn = get_connection()
    try:
        return get_refresh_state(conn)
    finally:
        conn.close()


@lru_cache(maxsize=4)
def _cached_orders(cache_month: str) -> pd.DataFrame:
    return read_orders()


@lru_cache(maxsize=1)
def _cached_cocacola() -> pd.DataFrame:
    return load_cocacola_skus()


@lru_cache(maxsize=1)
def _cached_monster() -> pd.DataFrame:
    return load_monster_skus()


@lru_cache(maxsize=1)
def _cached_ferrera() -> pd.DataFrame:
    return load_ferrera_skus()


def load_brand_skus(brand: str) -> pd.DataFrame:
    if brand == "coca-cola":
        return _cached_cocacola().copy()
    if brand == "monster":
        return _cached_monster().copy()
    if brand == "ferrera":
        return _cached_ferrera().copy()
    raise KeyError(brand)


def load_orders_or_error():
    try:
        state = _load_refresh_state()
    except Exception as exc:
        return None, None, str(exc)
    cache_month = (state or {}).get("last_refreshed_month") or "none"
    try:
        raw = _cached_orders(cache_month)
    except Exception as exc:
        return None, state, f"Could not load order data from Postgres: {exc}"
    if raw is None or raw.empty:
        return None, state, (
            "No order rows in Postgres. Run the monthly refresh job "
            "(python refresh_orders.py / Railway cron) first."
        )
    return raw, state, None


def proposed_key(brand: str) -> str:
    return f"proposed_{brand}"


def rewards_key(brand: str) -> str:
    return f"rewards_{brand}"


def get_proposed(session, brand: str) -> dict[str, int]:
    raw = session.get(proposed_key(brand)) or {}
    return {str(k): int(v) for k, v in raw.items()}


def set_proposed(session, brand: str, mapping: dict[str, int]) -> None:
    session[proposed_key(brand)] = {str(k): int(v) for k, v in mapping.items()}


def get_rewards(session, brand: str) -> list[tuple[str, int]]:
    stored = session.get(rewards_key(brand))
    if stored:
        return [(str(n), int(p)) for n, p in stored]
    defaults = BRAND_DEFAULTS[brand]["rewards"]
    return list(defaults.items())


def set_rewards(session, brand: str, rewards: list[tuple[str, int]]) -> None:
    session[rewards_key(brand)] = [[n, int(p)] for n, p in rewards]


def enrich_skus(raw: pd.DataFrame, skus_df: pd.DataFrame) -> pd.DataFrame:
    valid = set(skus_df["sku"].dropna().astype(str))
    pen = compute_store_penetration(raw, valid)
    out = skus_df.copy()
    out["sku"] = out["sku"].astype(str)
    out = out.merge(pen, on="sku", how="left")
    out["store_penetration"] = out["store_penetration"].fillna(0).astype(int)
    out["current_points"] = out["current_points"].fillna(0).astype(int)
    return out


def apply_proposed_to_rows(skus_df: pd.DataFrame, proposed: dict[str, int]) -> list[dict]:
    rows = []
    for _, row in skus_df.iterrows():
        sku = str(row["sku"])
        title = row["product_title"]
        proposed_pts = proposed.get(sku, proposed.get(title, 0))
        entry = {
            "sku": sku,
            "product_title": title,
            "store_penetration": int(row["store_penetration"]),
            "current_points": int(row["current_points"]),
            "proposed_points": int(proposed_pts or 0),
        }
        for col in ("size", "brand", "category"):
            if col in row.index and pd.notna(row[col]):
                entry[col] = row[col]
        rows.append(entry)
    return rows


def parse_month(month_label: str) -> tuple[int, int]:
    period = pd.Period(month_label)
    return int(period.year), int(period.month)


def parse_reward_form(form) -> list[tuple[str, int]]:
    names = form.getlist("reward_name")
    points = form.getlist("reward_points")
    rewards = []
    for name, pts in zip(names, points):
        name = (name or "").strip()
        if not name:
            continue
        try:
            rewards.append((name, int(float(pts))))
        except (TypeError, ValueError):
            rewards.append((name, 0))
    return rewards


def parse_proposed_form(form) -> dict[str, int]:
    skus = form.getlist("sku")
    proposed_vals = form.getlist("proposed_points")
    out = {}
    for sku, pts in zip(skus, proposed_vals):
        sku = str(sku)
        try:
            value = int(float(pts or 0))
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            out[sku] = value
    return out


@app.get("/", response_class=HTMLResponse)
def home():
    return RedirectResponse(url="/brands/coca-cola", status_code=302)


@app.get("/health")
def health():
    return {"ok": True}


def run_brand_simulation(raw, skus_df, month_label: str, proposed: dict, rewards: list[tuple[str, int]]):
    year, mon = parse_month(month_label)
    valid = set(skus_df["sku"].astype(str))
    points_lookup = build_points_lookup(skus_df, proposed)
    sku_to_title = dict(zip(skus_df["sku"].astype(str), skus_df["product_title"]))
    month_orders = get_month_orders(raw, year, mon, valid)
    month_orders = month_orders.merge(skus_df[["sku", "product_title"]], on="sku", how="inner")
    reward_thresholds = dict(rewards)
    store_points = simulate(month_orders, sku_to_title, points_lookup, reward_thresholds)
    return summarize_results(store_points, reward_thresholds)


def brand_page_context(
    request: Request,
    brand: str,
    raw,
    state,
    selected_month: str,
    months: list[str],
    skus_df,
    proposed: dict,
    rewards: list[tuple[str, int]],
    search: str = "",
    flash=None,
    bulk_value: int = 100,
):
    rows = apply_proposed_to_rows(skus_df, proposed)
    if search:
        needle = search.lower()
        rows = [
            r for r in rows
            if needle in r["sku"].lower() or needle in str(r["product_title"]).lower()
        ]

    freshness = None
    if state and state.get("last_refreshed_month"):
        freshness = format_month_label(state["last_refreshed_month"])

    results = run_brand_simulation(raw, skus_df, selected_month, proposed, rewards)

    return {
        "brand": brand,
        "brand_label": BRAND_DEFAULTS[brand]["label"],
        "extra_cols": BRAND_DEFAULTS[brand]["extra_cols"],
        "months": months,
        "month_options": [(m, format_month_label(m)) for m in months],
        "selected_month": selected_month,
        "selected_month_label": format_month_label(selected_month),
        "rows": rows,
        "rewards": rewards,
        "search": search,
        "freshness": freshness,
        "results": results,
        "flash": flash,
        "brands": BRAND_DEFAULTS,
        "bulk_value": bulk_value,
    }


@app.get("/brands/{brand}", response_class=HTMLResponse)
def brand_page(request: Request, brand: str, month: str | None = None, q: str | None = None):
    brand = brand.lower()
    if brand not in BRAND_DEFAULTS:
        return TEMPLATES.TemplateResponse(
            request,
            "error.html",
            {"title": "Unknown brand", "message": f"Unknown brand: {brand}"},
            status_code=404,
        )

    raw, state, error = load_orders_or_error()
    if error:
        return TEMPLATES.TemplateResponse(
            request,
            "error.html",
            {"title": "Data unavailable", "message": error},
            status_code=503,
        )

    skus_df = enrich_skus(raw, load_brand_skus(brand))
    valid = set(skus_df["sku"].astype(str))
    months = available_months(raw, valid)
    if not months:
        return TEMPLATES.TemplateResponse(
            request,
            "error.html",
            {"title": "No months", "message": "No order months available for this brand."},
            status_code=404,
        )

    selected_month = month if month in months else months[-1]
    proposed = get_proposed(request.session, brand)
    rewards = get_rewards(request.session, brand)
    flash = request.session.pop("flash", None)

    return TEMPLATES.TemplateResponse(
        request,
        "brand.html",
        brand_page_context(
            request,
            brand,
            raw,
            state,
            selected_month,
            months,
            skus_df,
            proposed,
            rewards,
            search=(q or "").strip(),
            flash=flash,
        ),
    )


@app.post("/brands/{brand}/simulate", response_class=HTMLResponse)
async def simulate_brand(
    request: Request,
    brand: str,
    month: str = Form(...),
    q: str = Form(""),
    bulk_value: int = Form(100),
    action: str = Form("simulate"),
):
    brand = brand.lower()
    if brand not in BRAND_DEFAULTS:
        return RedirectResponse(url="/", status_code=302)

    form = await request.form()
    rewards = parse_reward_form(form)
    if not rewards:
        rewards = list(BRAND_DEFAULTS[brand]["rewards"].items())
    set_rewards(request.session, brand, rewards)

    proposed = parse_proposed_form(form)

    if action == "bulk_apply":
        selected = set(form.getlist("selected_sku"))
        for sku in selected:
            proposed[str(sku)] = int(bulk_value)
        set_proposed(request.session, brand, proposed)
        request.session["flash"] = f"Applied {bulk_value} points to {len(selected)} SKUs."
        qs = f"?month={month}"
        if q:
            qs += f"&q={q}"
        return RedirectResponse(url=f"/brands/{brand}{qs}", status_code=303)

    if action == "add_reward":
        new_name = (form.get("new_reward_name") or "").strip() or f"Reward {len(rewards) + 1}"
        try:
            new_pts = int(float(form.get("new_reward_points") or 5000))
        except (TypeError, ValueError):
            new_pts = 5000
        rewards.append((new_name, new_pts))
        set_rewards(request.session, brand, rewards)
        set_proposed(request.session, brand, proposed)
        return RedirectResponse(url=f"/brands/{brand}?month={month}", status_code=303)

    if action.startswith("remove_reward_"):
        idx = int(action.split("_")[-1])
        if 0 <= idx < len(rewards):
            rewards.pop(idx)
        set_rewards(request.session, brand, rewards)
        set_proposed(request.session, brand, proposed)
        return RedirectResponse(url=f"/brands/{brand}?month={month}", status_code=303)

    set_proposed(request.session, brand, proposed)

    raw, state, error = load_orders_or_error()
    if error:
        return TEMPLATES.TemplateResponse(
            request,
            "error.html",
            {"title": "Data unavailable", "message": error},
            status_code=503,
        )

    skus_df = enrich_skus(raw, load_brand_skus(brand))
    valid = set(skus_df["sku"].astype(str))
    months = available_months(raw, valid)
    selected_month = month if month in months else months[-1]

    return TEMPLATES.TemplateResponse(
        request,
        "brand.html",
        brand_page_context(
            request,
            brand,
            raw,
            state,
            selected_month,
            months,
            skus_df,
            proposed,
            rewards,
            search=(q or "").strip(),
            bulk_value=bulk_value,
        ),
    )


@app.post("/brands/{brand}/import")
async def import_points(
    request: Request,
    brand: str,
    month: str = Form(...),
    file: UploadFile = File(...),
):
    brand = brand.lower()
    if brand not in BRAND_DEFAULTS:
        return RedirectResponse(url="/", status_code=302)

    content = await file.read()
    points_map, error = parse_imported_points(content, file.filename or "upload.csv")
    if error:
        request.session["flash"] = error
        return RedirectResponse(url=f"/brands/{brand}?month={month}", status_code=303)

    skus_df = load_brand_skus(brand)
    valid = set(skus_df["sku"].dropna().astype(str))
    sku_to_title = dict(zip(skus_df["sku"].astype(str), skus_df["product_title"]))
    proposed = get_proposed(request.session, brand)
    matched = 0
    for sku, pts in points_map.items():
        if sku in valid:
            proposed[sku] = int(pts)
            matched += 1
    set_proposed(request.session, brand, proposed)
    skipped = len(points_map) - matched
    msg = f"Imported points for {matched} SKUs."
    if skipped:
        msg += f" {skipped} SKUs skipped (not in this brand)."
    if matched == 0:
        msg = "No matching SKUs found in the uploaded file."
    request.session["flash"] = msg
    return RedirectResponse(url=f"/brands/{brand}?month={month}", status_code=303)


@app.get("/brands/{brand}/export")
def export_skus(request: Request, brand: str):
    brand = brand.lower()
    if brand not in BRAND_DEFAULTS:
        return RedirectResponse(url="/", status_code=302)

    raw, _state, error = load_orders_or_error()
    if error:
        return Response(error, status_code=503, media_type="text/plain")

    skus_df = enrich_skus(raw, load_brand_skus(brand))
    proposed = get_proposed(request.session, brand)
    rows = apply_proposed_to_rows(skus_df, proposed)
    export = pd.DataFrame(rows)
    cols = ["sku", "product_title"] + BRAND_DEFAULTS[brand]["extra_cols"] + [
        "store_penetration", "current_points", "proposed_points",
    ]
    cols = [c for c in cols if c in export.columns]
    csv_data = export[cols].to_csv(index=False)
    filename = f"{brand}_sku_export.csv"
    return Response(
        csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
