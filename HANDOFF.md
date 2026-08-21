# Handoff: Create a new brand from a modal (P4)

**Owner:** Rick (PM)  
**Repo:** `rickrangel-mer/m-rewards-simulator`  
**Product list:** [`backlog.md`](backlog.md)  
**This brief:** P4 only. Do not reintroduce Streamlit. Do not change `start.sh` web vs `SERVICE_ROLE=refresh`. Do not add logos. Do not trigger Athena from the website.

---

## Baseline (read this before branching)

| Item | Status |
|---|---|
| **P0** proposed points + rewards in Postgres | **On `main`** — [PR #6](https://github.com/rickrangel-mer/m-rewards-simulator/pull/6) |
| **P1** web-managed SKU catalogs (`brand_skus`) | **On `main`** — landed via [PR #9](https://github.com/rickrangel-mer/m-rewards-simulator/pull/9) (rebase of #7 onto `main`) |
| **P2** store-inclusion review | **On `main`** — [PR #9](https://github.com/rickrangel-mer/m-rewards-simulator/pull/9). Denominator is **A** (current-month brand orderers). `SUM(li.initial_quantity)` stays. Caption + tests. Athena SQL unchanged. |
| **P3** per-brand color | **On `main`** — [PR #9](https://github.com/rickrangel-mer/m-rewards-simulator/pull/9), palettes [PR #10](https://github.com/rickrangel-mer/m-rewards-simulator/pull/10), two-color split [PR #11](https://github.com/rickrangel-mer/m-rewards-simulator/pull/11) |
| **P4** | This handoff |

**Start from `origin/main`.** Catalogs, proposed points, rewards, and the three brand palettes are already there. Nav and `load_brand_skus` still key off hardcoded [`BRAND_DEFAULTS`](simulator.py) / [`CATALOG_BRANDS`](data.py) (`coca-cola`, `monster`, `ferrera`). That is the P4 problem.

---

## What this product is

Internal simulator for Mercaso M-Rewards. For a brand, pick a month of historical orders, set points per SKU and reward thresholds, and see how many stores would earn each reward.

**Data path:**
- Order snapshots in Railway Postgres (`orders`, `refresh_state`)
- Monthly Athena pull via cron (`SERVICE_ROLE=refresh` → `refresh_orders.py`)
- Web app never talks to Athena
- FastAPI + Jinja2; sectioned brand page (month+results, SKU tools, reward thresholds)
- SKU lists live in `brand_skus`; Excel is bulk-edit (upload/download), not live config

---

## P4 — New brand from a modal (not a code template)

Rick’s wording: **“template” means a modal** that lets an operator create a new brand page. It is **not** a developer copy-paste of `BRAND_DEFAULTS` and a new git xlsx.

There is no new-brand UI today. Adding a fourth brand requires a code change (`BRAND_DEFAULTS`, `CATALOG_BRANDS`, `load_brand_skus` / seed, nav loop in [`templates/base.html`](templates/base.html)). After P4, an operator should do it from the website.

### What the modal must collect

Show a short **requirements list** in the modal so the operator knows what is needed before submit. Required:

| Field | Notes |
|---|---|
| **Display name** | e.g. `Pepsi`. Shown in the nav and `<h1>`. |
| **URL slug** | e.g. `pepsi`. Lowercase `[a-z0-9-]+`. Unique. Prefill from the display name; operator can edit. Must not collide with `coca-cola` / `monster` / `ferrera` or another saved brand. |
| **Participating SKUs Excel/CSV** | Canonical catalog upload: `sku`, `product_title`, `current_points` (optional `size`, `brand`, `category`). Reuse [`parse_catalog_file()`](data.py). Same format as **Download catalog Excel** on an existing brand page. This is the participating-SKU list for simulation and for the next Athena refresh (`load_all_skus()`). |
| **At least one reward** | Name + point cutoff (same idea as the Reward Thresholds section). One row is enough; allow adding more in the modal. |

Optional:

| Field | Notes |
|---|---|
| **Palette** | Reuse a P3 theme: Coca-Cola (red/white), Monster (black + lime), Ferrera/Nerds (pink/purple/blue), or default teal. Do **not** invent a hex picker. |

Do **not** require a git workbook. Do **not** ask for a logo.

### After save

1. Persist the brand in a **Postgres registry** (new `brands` table or equivalent: slug, label, theme, sort).
2. Write the uploaded catalog to `brand_skus` for that slug (same path as catalog replace).
3. Write the reward name(s) + cutoff(s) to `brand_rewards`.
4. Nav in [`templates/base.html`](templates/base.html) lists **registry brands**, not `BRAND_DEFAULTS` keys.
5. Redirect to `/brands/<slug>`. The page uses the **existing** sectioned [`templates/brand.html`](templates/brand.html) (month + results, SKU tools, reward thresholds). No new simulator layout.
6. Seed the original three brands into the registry on boot if it is empty (from `BRAND_DEFAULTS`), so Coca-Cola / Monster / Ferrera keep working without a manual migrate.

### Simulation / Athena

- Website still never talks to Athena.
- `refresh_orders.py` already uses `load_all_skus()` over `brand_skus`. New participating SKUs are included on the **next** cron (or a manual refresh job).
- If the uploaded SKUs already exist in `orders` (they were on another brand’s catalog, or a previous pull), the new page can simulate immediately.
- If they are net-new SKUs, the brand page exists but results stay empty until refresh. Say that in the modal (one line under the SKU upload). Do **not** add a “Run Athena” button.

### Theme hook (needed so a fourth slug is not stuck on teal)

Today CSS is `body[data-brand="coca-cola"]` etc. A new slug will not match. Smallest fix: set `data-theme="{{ theme }}"` on `<body>` (existing three map 1:1: `coca-cola` / `monster` / `ferrera`) and point the P3 blocks at `body[data-theme="..."]`. Keep `data-brand="{{ brand }}"` for tests and debugging. Default / unknown theme stays `:root` teal.

### UX sketch

- **New brand** control in the topbar next to the brand nav (every page that uses [`templates/base.html`](templates/base.html)).
- Native `<dialog>` (or equivalent). No React, no Streamlit. A few lines of JS to `.showModal()` is fine.
- One modal, not a multi-step wizard. Checklist of requirements at the top, then the fields, then Create.
- Validate server-side: missing file, catalog parse error, empty name, bad/duplicate slug, zero rewards → flash and stay put (re-open modal or redirect with flash).
- Existing catalog **Preview / Merge / Replace** on the brand page stays for later edits. The create modal can write the catalog in one shot (replace into empty).

### Implementation notes

- Replace hardcoded `if brand not in BRAND_DEFAULTS` gates with “slug in registry.”
- `CATALOG_BRANDS` / `seed_brand_catalogs()` should keep seeding **only** the original three git xlsx files. New brands are operator-uploaded, never seeded from git.
- `extra_cols` already comes from catalog columns when present ([`extra_cols_for()`](app.py)); new brands do not need a hardcoded extra-col list.
- Default rewards in `BRAND_DEFAULTS` remain fallbacks for the original three if `brand_rewards` is empty. A newly created brand always has the rewards from the modal.
- Flash stays on the session cookie. Registry / catalogs / rewards stay in Postgres.
- Keep layout/sectioning. Keep the P3 palettes. Keep store-inclusion captions.

### P4 tests

- Prefer the existing FakeStore pattern in [`test_app.py`](test_app.py). No live Railway or Athena.
- GET a brand page → modal markup includes the requirements (display name, slug, SKU Excel, at least one reward).
- POST a valid create → registry + catalog + rewards stored; nav lists the new label; `GET /brands/<slug>` is 200 and still sectioned (`simulation-results`, `sku-points`, `reward-thresholds`).
- Missing SKU file / unparseable Excel / duplicate slug / no reward → 4xx or redirect+flash; no half-written brand.
- Coca-Cola / Monster / Ferrera still render sectioned; `data-brand` still present; existing catalog upload tests still pass.
- `pytest` green.

### P4 out of scope

Deleting or renaming a brand, uploading logos, hex color picker, auth, Streamlit, changing `start.sh`, calling Athena from the web, a fourth git Excel workbook, a separate “brand CMS” site.

---

## Shared constraints

- FastAPI + Jinja2 only.
- `pytest` must pass without a real Railway DB or Athena.
- Smoke `coca-cola`, `monster`, `ferrera` plus the new slug in tests.
- Do not edit git Excel workbooks as live config.

---

## Suggested order

1. `brands` registry + seed the original three + `data-theme` so palettes are reusable.
2. Swap nav / `brand not in BRAND_DEFAULTS` to the registry.
3. Topbar **New brand** + `<dialog>` with the requirements list and fields.
4. POST handler: parse catalog, save registry + `brand_skus` + `brand_rewards`, redirect.
5. Tests, `pytest`, commit, push, open a PR.

---

## Acceptance

1. Operator opens **New brand**, sees the requirements (including participating SKUs Excel), fills them, and gets a new nav item + sectioned brand page without a code change.
2. Coca-Cola / Monster / Ferrera still work; no Streamlit; `start.sh` roles unchanged.
3. New SKUs are in `brand_skus` (and therefore the next Athena `load_all_skus()`). The UI explains they will not simulate until orders for those SKUs exist in Postgres.
4. Palette choice reuses P3 themes via `data-theme`. Layout/sectioning unchanged.

---

## Paste-ready agent prompt

```
Read HANDOFF.md and implement P4 only.

Baseline: origin/main. P0–P3 are on main (catalogs, store-inclusion A, per-brand colors). Do not change start.sh web vs SERVICE_ROLE=refresh. Do not reintroduce Streamlit. Do not add logos. Do not call Athena from the web.

P4: “Template” means an operator modal, not a developer code template. Add a New brand control that opens a modal listing requirements: display name, URL slug, participating SKUs Excel/CSV (canonical sku / product_title / current_points, reuse parse_catalog_file), and at least one reward name + cutoff. Optional: pick an existing P3 palette (coca-cola / monster / ferrera / default). Persist a Postgres brand registry; seed the original three if empty. Nav reads the registry. Redirect to /brands/<slug> using the existing sectioned brand.html. New SKUs appear in simulations after the next Athena refresh (or immediately if those SKUs already have rows in orders). Point CSS at data-theme so a new slug can reuse palettes.

pytest must pass without Railway/Athena. Smoke coca-cola, monster, ferrera, plus creating a fourth brand in FakeStore. Commit, push, open a PR.
```
