# Handoff: Persist proposed points and rewards (P0)

**Owner:** Rick (PM)  
**Repo:** `rickrangel-mer/m-rewards-simulator`  
**Branch baseline:** `main` (FastAPI site live; page sectioning in PR #3)  
**Next focus:** Save SKU proposed points, reward names, and reward thresholds in Railway Postgres so they survive refresh and are shared with a coworker  
**Product list:** [`backlog.md`](backlog.md) (this is P0 only)

---

## What this product is

Internal simulator for Mercaso M-Rewards. For a brand (Coca-Cola / Monster / Ferrera), pick a month of historical orders, set points per SKU and reward thresholds, and see how many stores would earn each reward.

**Data today:**
- Order snapshots in Railway Postgres (`orders`, `refresh_state`)
- Monthly Athena pull via cron (`SERVICE_ROLE=refresh` → `refresh_orders.py`)
- Brand SKU **lists** and **current** points in Excel workbooks in the repo
- Proposed points + reward edits in a **12-hour browser cookie** (`SessionMiddleware` in [`app.py`](app.py), `max_age=60 * 60 * 12`)
- Web app never talks to Athena

---

## The problem

A coworker opens a brand page, types proposed points / reward names / cutoffs, and hits Update Simulation. That only lives in **their** session cookie. After ~12 hours, a new browser, a Railway web redeploy that drops cookies, or a teammate opening the same URL, the site falls back to Excel `current_points` and `BRAND_DEFAULTS` rewards. They have to re-enter everything.

Import CSV/Excel overlays have the same fate: session-only.

---

## Target (P0 only)

Postgres is the source of truth for **working edits**:

- Per-brand map of `sku → proposed_points`
- Per-brand list of `(reward_name, threshold_points)` in display order

**Shared across users** (no login, last write wins). Rick and his coworker should see the same numbers after either of them saves.

**Still from git Excel (do not move in P0):**
- Which SKUs exist on the page
- `current_points` column
- Extra columns (Monster size, Ferrera brand/category)
- Athena refresh SKU union (`load_all_skus()`)

Replacing those workbooks with web-managed catalogs is **P1** in [`backlog.md`](backlog.md). Do not do P1 now.

---

## Suggested data model

Extend [`SCHEMA_SQL`](data.py) / `init_schema()` (today it only creates `orders` and `refresh_state`):

```sql
CREATE TABLE IF NOT EXISTS brand_proposed_points (
    brand      text        NOT NULL,
    sku        text        NOT NULL,
    points     int         NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT NOW(),
    PRIMARY KEY (brand, sku)
);

CREATE TABLE IF NOT EXISTS brand_rewards (
    brand      text        NOT NULL,
    sort       int         NOT NULL,
    name       text        NOT NULL,
    points     int         NOT NULL,
    PRIMARY KEY (brand, sort)
);
```

Equivalent shape is fine. Keep it boring.

**Empty proposed row for a SKU** → simulation uses Excel `current_points` (`build_points_lookup()` already treats proposed `> 0` as override).

**No reward rows for a brand** → keep serving `BRAND_DEFAULTS[brand]["rewards"]` until someone adds/removes/edits rewards, then persist the full list.

---

## Critical: merge, do not replace from the HTML form

The SKU table can be **filtered by search**. POST `/brands/{brand}/simulate` only includes `sku` / `proposed_points` for **visible rows**. Today `parse_proposed_form()` + `set_proposed()` **replace the entire session map**, so “search then Update Simulation” can wipe proposed points for SKUs not on screen.

When you persist:

- **Proposed points:** treat the form (and bulk apply, and import) as a **patch**. Load existing Postgres map, overlay posted SKUs, write back. SKUs omitted from the form must keep their stored points.
- **Rewards:** the form posts every reward card (not filtered). Full replace of that brand’s reward rows is OK.

`parse_proposed_form()` currently **drops points ≤ 0**. Keep that: storing `0` should remove the override so Excel current points apply again. Merge must delete that SKU’s row (or store 0 and ignore it in lookup — deleting is cleaner).

---

## Wiring (keep UX)

Swap session get/set in [`app.py`](app.py) (`get_proposed` / `set_proposed` / `get_rewards` / `set_rewards`) to Postgres. Keep **flash** in the session cookie.

Call sites that must write through to Postgres:

| Action | Route |
|---|---|
| Update Simulation | POST `/brands/{brand}/simulate` `action=simulate` |
| Bulk apply | same, `action=bulk_apply` |
| Add / remove reward | same, `add_reward` / `remove_reward_*` |
| Import proposed points | POST `/brands/{brand}/import` |
| Page load / export | GET `/brands/{brand}` and `/export` must **read** Postgres |

No new screens required. Optional one-line caption (“Saved for everyone”) is nice, not required.

Sectioned layout in [`templates/brand.html`](templates/brand.html) stays as-is (month with results; SKU tools with editor; reward controls with thresholds).

---

## Schema init on the **web** service

`init_schema()` runs today only inside [`refresh_orders.py`](refresh_orders.py) (cron). The web process may boot for weeks without a refresh.

**The FastAPI app must create the new tables itself** (startup or first request), using the same `DATABASE_URL` it already uses for `orders`. Do not wait for cron. Do not change `start.sh` web vs `SERVICE_ROLE=refresh` split.

---

## Tests

Existing [`test_app.py`](test_app.py) uses `TestClient` session cookies and mocks `load_orders_or_error` / `load_brand_skus`. After this change, persist must not require a real Railway DB in unit tests.

- Fake/in-memory store behind `get_proposed`/`set_proposed`/`get_rewards`/`set_rewards`, **or** patch the new data-layer functions.
- Assert: POST simulate → new TestClient (no cookies) → GET still shows saved proposed points and reward names.
- Assert: search-filtered POST does **not** delete proposed points for SKUs not in the form.
- Assert: import still merges; add/remove reward still round-trips.
- Smoke: all three brand keys (`coca-cola`, `monster`, `ferrera`).
- Run `pytest`. Do not add Athena/Postgres/cron tests unless you touch those paths.

---

## Out of scope

- Replacing git Excel workbooks / unifying loaders (P1)
- Athena query / store-inclusion rules (P2)
- Per-brand colors (P3)
- “Create a new brand” UI (P4)
- Streamlit
- Auth / per-user sandboxes (shared last-write-wins is the product)
- Changing `start.sh` roles or the Athena cron

---

## Acceptance

1. Edit proposed points, click Update Simulation, hard-refresh or open another browser: values still there.
2. Same for reward names, thresholds, add reward, remove reward.
3. Import CSV/Excel overlay persists the same way.
4. Coworker on another machine sees those values (shared Postgres, not a cookie).
5. Excel still defines the SKU list and current-points column.
6. Search + save does not wipe hidden SKUs.
7. `pytest` green. Brand pages still sectioned. Railway web redeploy picks up schema + code; cron unchanged.

---

## Suggested first steps

1. Add tables to `SCHEMA_SQL` and load/save helpers in [`data.py`](data.py).
2. Ensure `init_schema()` (or equivalent) runs from the web app on startup.
3. Point `get_proposed` / `set_proposed` / `get_rewards` / `set_rewards` at Postgres; **merge** proposed-point writes.
4. Leave flash on the session cookie.
5. Fix/extend [`test_app.py`](test_app.py) with a fake store and a no-cookie round-trip.
6. Smoke-test all three brands locally if `DATABASE_URL` is available; otherwise tests + a Railway web redeploy after merge.

---

## Deploy reminder

After merge: **redeploy the Railway web service** (it has `DATABASE_URL`). Cron/refresh service unchanged unless you edit `start.sh` / env vars (you should not). First web boot must `CREATE TABLE IF NOT EXISTS` the new tables.

---

## Paste-ready agent prompt

```
Read HANDOFF.md and implement P0 only (persist proposed SKU points, reward names, and thresholds in Railway Postgres).

Baseline: main. Do not do backlog P1–P4. Do not change Athena/Postgres orders pipeline, start.sh web vs refresh split, or reintroduce Streamlit. Do not replace the git Excel workbooks.

Today get_proposed/set_proposed and get_rewards/set_rewards in app.py use a 12-hour session cookie. Move working edits to shared Postgres (new tables via SCHEMA_SQL / init_schema). Excel still owns SKU lists and current_points. Empty proposed → Excel current; empty rewards → BRAND_DEFAULTS.

MERGE proposed-point writes: the SKU table can be search-filtered, so a simulate POST is not a full snapshot. Overlay posted SKUs onto the existing map; omitted SKUs keep stored points. Points ≤ 0 remove the override. Rewards may full-replace. Flash can stay on the session.

Web app must create the new tables on startup (init_schema currently runs only in refresh_orders.py). Keep UX/sectioning. pytest must pass without real Railway DB (fake store or mocks), including a no-cookie round-trip and a search-filtered POST that does not wipe other SKUs. Smoke all three brands.

Start from main, implement, run pytest, commit/push, open a PR.
```
