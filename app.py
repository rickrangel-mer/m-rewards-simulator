"""FastAPI + Jinja2 M-Rewards simulator (replaces Streamlit)."""

from __future__ import annotations

import io
import json
import os
import re
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from data import (
    CATALOG_EXTRA_COLS,
    PALETTE_THEMES,
    DuplicateBrandError,
    catalog_download_frame,
    catalog_frame,
    catalog_template_frame,
    create_brand,
    diff_catalog,
    ensure_schema,
    format_month_label,
    get_brand,
    get_connection,
    get_refresh_state,
    load_brand_rewards,
    load_brands,
    load_catalog_skus,
    load_proposed_points,
    merge_catalog_skus,
    merge_proposed_points,
    parse_catalog_file,
    read_orders,
    replace_catalog_skus,
    save_brand_rewards,
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
from refresh_orders import run_brand_refresh

BASE_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RESERVED_SLUGS = frozenset({
    "create", "new", "export", "import", "catalog", "simulate", "health", "static",
})


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_schema()
    yield


app = FastAPI(title="M-Rewards Simulator", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "m-rewards-dev-secret-change-me"),
    max_age=60 * 60 * 12,
)


def _brand_from_path(path: str) -> str | None:
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "brands":
        return parts[1]
    return None


@app.exception_handler(RequestValidationError)
async def form_validation_to_flash(request: Request, exc: RequestValidationError):
    """HTML/multipart form posts should flash and redirect, not return JSON 422."""
    content_type = request.headers.get("content-type", "")
    is_form = request.method == "POST" and (
        "multipart/form-data" in content_type
        or "application/x-www-form-urlencoded" in content_type
    )
    brand = _brand_from_path(request.url.path)
    if not is_form or not brand:
        return await request_validation_exception_handler(request, exc)

    missing = [str(err.get("loc", ["?"])[-1]) for err in exc.errors() if err.get("type") == "missing"]
    if "file" in missing:
        message = "Choose a CSV or Excel file before importing."
    elif "month" in missing:
        message = "Import did not include the simulation month. Use the Import button on the brand page."
    else:
        message = "Could not process that upload. Check the file and try again."
    request.session["flash"] = message
    return RedirectResponse(url=f"/brands/{brand}", status_code=303)


def _load_refresh_state():
    conn = get_connection()
    try:
        return get_refresh_state(conn)
    finally:
        conn.close()


@lru_cache(maxsize=4)
def _cached_orders(cache_month: str) -> pd.DataFrame:
    return read_orders()


def load_brand_skus(brand: str) -> pd.DataFrame:
    return catalog_frame(load_catalog_skus(brand))


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")


def registry_map() -> dict[str, dict]:
    return {
        row["slug"]: {"label": row["label"], "theme": row["theme"] or "default"}
        for row in load_brands()
    }


def page_chrome(brand: str | None = None) -> dict:
    brands = registry_map()
    meta = brands.get(brand or "") or {}
    theme = meta.get("theme") or "default"
    if theme not in PALETTE_THEMES:
        theme = "default"
    return {
        "brand": brand or "",
        "brand_label": meta.get("label", ""),
        "theme": theme,
        "brands": brands,
        "palettes": PALETTE_THEMES,
    }


def extra_cols_for(brand: str, skus_df: pd.DataFrame) -> list[str]:
    cols = list(BRAND_DEFAULTS.get(brand, {}).get("extra_cols", []))
    for col in CATALOG_EXTRA_COLS:
        if col in cols or col not in skus_df.columns:
            continue
        series = skus_df[col]
        if series.notna().any() and (series.astype(str).str.strip() != "").any():
            cols.append(col)
    return cols


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


def get_proposed(brand: str) -> dict[str, int]:
    return load_proposed_points(brand)


def set_proposed(brand: str, patch: dict[str, int]) -> dict[str, int]:
    """Overlay `patch` onto stored proposed points. Values ≤ 0 drop the override."""
    return merge_proposed_points(brand, patch)


def get_rewards(brand: str) -> list[tuple[str, int]]:
    stored = load_brand_rewards(brand)
    if stored:
        return [(str(n), int(p)) for n, p in stored]
    defaults = BRAND_DEFAULTS.get(brand, {}).get("rewards") or {}
    return list(defaults.items())


def empty_results(rewards: list[tuple[str, int]] | None = None) -> dict:
    return {
        "total_stores": 0,
        "avg_points": 0.0,
        "median_points": 0.0,
        "max_points": 0.0,
        "rewards": [
            {"name": name, "threshold": int(pts), "count": 0, "pct": 0.0}
            for name, pts in (rewards or [])
        ],
        "stores": [],
        "histogram": [],
    }


def set_rewards(brand: str, rewards: list[tuple[str, int]]) -> None:
    save_brand_rewards(brand, rewards)


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


def parse_proposed_patch(form) -> dict[str, int]:
    """All posted SKUs. Values ≤ 0 mean 'remove override' when merged."""
    skus = form.getlist("sku")
    proposed_vals = form.getlist("proposed_points")
    out = {}
    for sku, pts in zip(skus, proposed_vals):
        sku = str(sku)
        try:
            value = int(float(pts or 0))
        except (TypeError, ValueError):
            value = 0
        out[sku] = value
    return out


def parse_proposed_form(form) -> dict[str, int]:
    return {sku: pts for sku, pts in parse_proposed_patch(form).items() if pts > 0}


@app.get("/", response_class=HTMLResponse)
def home():
    return RedirectResponse(url="/brands/coca-cola", status_code=302)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/catalog-template.xlsx")
def download_catalog_template():
    buffer = io.BytesIO()
    catalog_template_frame().to_excel(buffer, index=False)
    return Response(
        buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="brand_sku_template.xlsx"'},
    )


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

    if selected_month:
        results = run_brand_simulation(raw, skus_df, selected_month, proposed, rewards)
    else:
        results = empty_results(rewards)

    ctx = page_chrome(brand)
    ctx.update({
        "extra_cols": extra_cols_for(brand, skus_df),
        "months": months,
        "month_options": [(m, format_month_label(m)) for m in months],
        "selected_month": selected_month,
        "selected_month_label": (
            format_month_label(selected_month) if selected_month else "no order months yet"
        ),
        "rows": rows,
        "rewards": rewards,
        "search": search,
        "freshness": freshness,
        "results": results,
        "flash": flash,
        "bulk_value": bulk_value,
        "open_new_brand": False,
    })
    return ctx


@app.get("/brands/{brand}", response_class=HTMLResponse)
def brand_page(request: Request, brand: str, month: str | None = None, q: str | None = None):
    brand = brand.lower()
    if get_brand(brand) is None:
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
    selected_month = month if month in months else (months[-1] if months else "")
    proposed = get_proposed(brand)
    rewards = get_rewards(brand)
    flash = request.session.pop("flash", None)
    open_new_brand = bool(request.session.pop("open_new_brand", False))

    ctx = brand_page_context(
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
    )
    ctx["open_new_brand"] = open_new_brand
    return TEMPLATES.TemplateResponse(request, "brand.html", ctx)


@app.post("/brands/{brand}/refresh-orders")
def refresh_brand_orders(request: Request, brand: str):
    brand = brand.lower()
    if get_brand(brand) is None:
        return RedirectResponse(url="/", status_code=302)

    skus_df = load_brand_skus(brand)
    sku_list = []
    if skus_df is not None and not skus_df.empty and "sku" in skus_df.columns:
        sku_list = [str(s) for s in skus_df["sku"].dropna().astype(str) if str(s).strip()]
    if not sku_list:
        request.session["flash"] = "Add participating SKUs before pulling order history."
        return RedirectResponse(url=f"/brands/{brand}", status_code=303)

    try:
        result = run_brand_refresh(brand, sku_list=sku_list)
    except Exception as exc:
        request.session["flash"] = f"Could not pull order history: {exc}"
        return RedirectResponse(url=f"/brands/{brand}", status_code=303)

    _cached_orders.cache_clear()
    first_label = format_month_label(result["start"].strftime("%Y-%m"))
    last_start = result["windows"][-1][0]
    last_label = format_month_label(last_start.strftime("%Y-%m"))
    request.session["flash"] = (
        f"Pulled {result['rows']:,} order rows for {result['sku_count']} SKUs "
        f"({first_label}–{last_label})."
    )
    return RedirectResponse(url=f"/brands/{brand}", status_code=303)


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
    if get_brand(brand) is None:
        return RedirectResponse(url="/", status_code=302)

    form = await request.form()
    rewards = parse_reward_form(form)
    if not rewards:
        rewards = list((BRAND_DEFAULTS.get(brand, {}).get("rewards") or {}).items())
    set_rewards(brand, rewards)

    patch = parse_proposed_patch(form)

    if action == "bulk_apply":
        selected = set(form.getlist("selected_sku"))
        for sku in selected:
            patch[str(sku)] = int(bulk_value)
        set_proposed(brand, patch)
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
        set_rewards(brand, rewards)
        set_proposed(brand, patch)
        return RedirectResponse(url=f"/brands/{brand}?month={month}", status_code=303)

    if action.startswith("remove_reward_"):
        idx = int(action.split("_")[-1])
        if 0 <= idx < len(rewards):
            rewards.pop(idx)
        set_rewards(brand, rewards)
        set_proposed(brand, patch)
        return RedirectResponse(url=f"/brands/{brand}?month={month}", status_code=303)

    proposed = set_proposed(brand, patch)

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
    selected_month = month if month in months else (months[-1] if months else "")

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
    month: str = Form(""),
    file: UploadFile = File(...),
):
    brand = brand.lower()
    if get_brand(brand) is None:
        return RedirectResponse(url="/", status_code=302)

    content = await file.read()
    points_map, error = parse_imported_points(content, file.filename or "upload.csv")
    if error:
        request.session["flash"] = error
        return RedirectResponse(url=f"/brands/{brand}{_month_query(month)}", status_code=303)

    skus_df = load_brand_skus(brand)
    valid = set(skus_df["sku"].dropna().astype(str))
    patch = {}
    matched = 0
    for sku, pts in points_map.items():
        if sku in valid:
            patch[sku] = int(pts)
            matched += 1
    if patch:
        set_proposed(brand, patch)
    skipped = len(points_map) - matched
    msg = f"Imported points for {matched} SKUs."
    if skipped:
        msg += f" {skipped} SKUs skipped (not in this brand)."
    if matched == 0:
        msg = "No matching SKUs found in the uploaded file."
    request.session["flash"] = msg
    return RedirectResponse(url=f"/brands/{brand}{_month_query(month)}", status_code=303)


@app.get("/brands/{brand}/export")
def export_skus(request: Request, brand: str):
    brand = brand.lower()
    if get_brand(brand) is None:
        return RedirectResponse(url="/", status_code=302)

    raw, _state, error = load_orders_or_error()
    if error:
        return Response(error, status_code=503, media_type="text/plain")

    skus_df = enrich_skus(raw, load_brand_skus(brand))
    proposed = get_proposed(brand)
    rows = apply_proposed_to_rows(skus_df, proposed)
    export = pd.DataFrame(rows)
    cols = ["sku", "product_title"] + extra_cols_for(brand, skus_df) + [
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


def _month_query(month: str, q: str = "") -> str:
    qs = f"?month={month}"
    if q:
        qs += f"&q={q}"
    return qs


@app.get("/brands/{brand}/catalog.xlsx")
def download_catalog(brand: str):
    brand = brand.lower()
    if get_brand(brand) is None:
        return RedirectResponse(url="/", status_code=302)

    records = load_catalog_skus(brand)
    df = catalog_download_frame(records)
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False)
    filename = f"{brand}_catalog.xlsx"
    return Response(
        buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/brands/{brand}/catalog", response_class=HTMLResponse)
async def preview_catalog_upload(
    request: Request,
    brand: str,
    month: str = Form(""),
    file: UploadFile = File(...),
):
    brand = brand.lower()
    if get_brand(brand) is None:
        return RedirectResponse(url="/", status_code=302)

    content = await file.read()
    incoming, error = parse_catalog_file(content, file.filename or "catalog.xlsx")
    if error:
        request.session["flash"] = error
        return RedirectResponse(url=f"/brands/{brand}{_month_query(month)}", status_code=303)

    existing = load_catalog_skus(brand)
    diff = diff_catalog(existing, incoming)
    payload = json.dumps(incoming, separators=(",", ":"))
    ctx = page_chrome(brand)
    ctx.update({
        "month": month,
        "filename": file.filename or "upload",
        "diff": diff,
        "payload": payload,
        "preview_limit": 25,
        "open_new_brand": False,
    })
    return TEMPLATES.TemplateResponse(request, "catalog_preview.html", ctx)


@app.post("/brands/{brand}/catalog/confirm")
async def confirm_catalog_upload(
    request: Request,
    brand: str,
    month: str = Form(""),
    action: str = Form(...),
    payload: str = Form(...),
):
    brand = brand.lower()
    if get_brand(brand) is None:
        return RedirectResponse(url="/", status_code=302)

    try:
        incoming = json.loads(payload)
        if not isinstance(incoming, list) or not incoming:
            raise ValueError("empty")
    except (TypeError, ValueError, json.JSONDecodeError):
        request.session["flash"] = "Catalog preview expired. Please upload the file again."
        return RedirectResponse(url=f"/brands/{brand}{_month_query(month)}", status_code=303)

    label = (get_brand(brand) or {}).get("label") or brand
    if action == "merge":
        merged = merge_catalog_skus(brand, incoming)
        request.session["flash"] = (
            f"Merged catalog: {len(incoming)} uploaded SKUs, "
            f"{len(merged)} total in {label}."
        )
    elif action == "replace":
        count = replace_catalog_skus(brand, incoming)
        request.session["flash"] = (
            f"Replaced catalog with {count} SKUs for {label}."
        )
    else:
        request.session["flash"] = "Catalog upload cancelled."
        return RedirectResponse(url=f"/brands/{brand}{_month_query(month)}", status_code=303)

    return RedirectResponse(url=f"/brands/{brand}{_month_query(month)}", status_code=303)


def _safe_return_to(value: str) -> str:
    path = (value or "").strip() or "/"
    if not path.startswith("/") or path.startswith("//"):
        return "/"
    return path


def _create_error(request: Request, message: str, return_to: str):
    request.session["flash"] = message
    request.session["open_new_brand"] = True
    return RedirectResponse(url=_safe_return_to(return_to), status_code=303)


@app.post("/brands/create")
async def create_brand_route(request: Request):
    form = await request.form()
    label = str(form.get("label") or "").strip()
    slug = slugify(label)
    theme = str(form.get("theme") or "default").strip().lower()
    return_to = str(form.get("return_to") or "/")
    if theme not in PALETTE_THEMES:
        theme = "default"
    rewards = parse_reward_form(form)

    if not label:
        return _create_error(request, "Display name is required.", return_to)
    if not SLUG_RE.fullmatch(slug) or slug in RESERVED_SLUGS:
        return _create_error(
            request,
            "Display name must include letters or numbers so a page URL can be assigned.",
            return_to,
        )
    if get_brand(slug) is not None:
        return _create_error(request, f"A brand named '{label}' already exists.", return_to)
    if not rewards:
        return _create_error(request, "Add at least one reward name and point cutoff.", return_to)

    upload = form.get("file")
    filename = getattr(upload, "filename", None) or ""
    if upload is None or not filename.strip() or not hasattr(upload, "read"):
        return _create_error(request, "Upload a participating SKUs Excel or CSV file.", return_to)

    content = await upload.read()
    incoming, error = parse_catalog_file(content, filename)
    if error:
        return _create_error(request, error, return_to)

    try:
        create_brand(slug, label, theme, incoming, rewards)
    except DuplicateBrandError:
        return _create_error(request, f"A brand named '{label}' already exists.", return_to)

    return RedirectResponse(url=f"/brands/{slug}", status_code=303)
