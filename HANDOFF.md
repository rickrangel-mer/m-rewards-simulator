# Handoff: Store inclusion (P2) and per-brand color (P3)

**Owner:** Rick (PM)  
**Repo:** `rickrangel-mer/m-rewards-simulator`  
**Product list:** [`backlog.md`](backlog.md)  
**This brief:** P2 and P3 only. Do not do P4. Do not reintroduce Streamlit. Do not change `start.sh` web vs `SERVICE_ROLE=refresh`.

---

## Baseline (read this before branching)

| Item | Status |
|---|---|
| **P0** proposed points + rewards in Postgres | **On `main`** — [PR #6](https://github.com/rickrangel-mer/m-rewards-simulator/pull/6) |
| **P1** web-managed SKU catalogs | **Merged, but not on `main`.** [PR #7](https://github.com/rickrangel-mer/m-rewards-simulator/pull/7) targeted `cursor/persist-proposed-rewards-1268` (P0’s branch). `#6` was squash-merged to `main`, so that P0 branch is **not** an ancestor of `main`. |
| **P2 / P3** | This handoff |

**Start from `origin/main`.** Before depending on catalog tables (`brand_skus`, catalog upload/download), **land P1 on `main` first**: rebase `origin/cursor/web-managed-catalogs-1268` onto `main` (do not merge the old P0 commit `cd63c58` — it duplicates squash `#6`) and open a PR. If P1 is already on `main` when you start, skip that.

After P1 is on `main`: SKU lists and `current_points` live in Postgres (`brand_skus`), seeded once from the git xlsx files. Proposed points / rewards stay in `brand_proposed_points` / `brand_rewards`. Excel is bulk-edit format, not live config.

---

## What this product is

Internal simulator for Mercaso M-Rewards. For a brand (Coca-Cola / Monster / Ferrera), pick a month of historical orders, set points per SKU and reward thresholds, and see how many stores would earn each reward.

**Data path:**
- Order snapshots in Railway Postgres (`orders`, `refresh_state`)
- Monthly Athena pull via cron (`SERVICE_ROLE=refresh` → `refresh_orders.py`)
- Web app never talks to Athena
- FastAPI + Jinja2; sectioned brand page (month+results, SKU tools, reward thresholds)

---

## P2 — Review store-inclusion logic

Rick’s concern: stores without “enough” purchases may be dropped, so reward “% of stores” and Total Stores look too optimistic or too small.

### Investigate first. Change the query only if the product rule is wrong.

There is **no** `HAVING` / min-quantity filter today. What *does* drop stores:

1. **Athena** [`fetch_order_data()`](data.py) — only rows for SKUs on the brand catalog (`li.sku IN (...)`). `SUM(li.initial_quantity)` as `total_quantity`. Latest `dt` partition on both line-item and order tables. **No** cancelled-order predicate, **no** net-quantity, **no** store-status filter.
2. **Simulator** [`get_month_orders()`](simulator.py) — selected calendar month, brand SKUs only.
3. **[`simulate()`](simulator.py)** — `groupby store_id` on those rows. A store is scored iff it ordered **≥1** of those SKUs in the selected month.
4. **“% of stores” / Total Stores** — that population, **not** all Mercaso stores, **not** “ever ordered this brand.”
5. **Store-level detail** table — top **500** by points (`summarize_results()`). Metrics and reward counts still use the full population.
6. **Histogram** — clips the **99th percentile for chart bins only**; averages / max / reward counts are unclipped.
7. **SKU “store penetration”** column — `nunique(store_id)` over **all months loaded in Postgres**, not the selected month.

`rewards_analysis.py` uses a different cohort idea (rewards SKUs vs all stores in the dump). Do not treat that script as the website rule.

### Product question to answer in the PR

Which denominator does Rick want?

| Option | Meaning |
|---|---|
| **A. Current-month orderers of this brand’s SKUs** | What the code does today |
| **B. Ever-ordered-this-brand** (any month in Postgres) | Stores with 0 points this month still in the % |
| **C. All Mercaso stores** | Needs a store universe Athena does not currently pull |

Also: is `SUM(li.initial_quantity)` the right qty (vs net, cancelled, returned)?

**If A is correct:** do not change Athena. Optionally add a one-line caption on the results panel so “Total Stores” / “% of stores” is not misread (e.g. “Stores that ordered this brand this month”). Add tests that document the rule (a store with no brand SKUs in the month is absent; histogram clip does not change `total_stores`).

**If B or C or quantity is wrong:** smallest code change that matches the rule. Keep `start.sh` split. SKU list for Athena is Postgres `load_all_skus()` once P1 is on `main` (Excel union only if you are still on pre-P1 `main`).

### P2 tests

- Prefer unit tests on `get_month_orders` / `simulate` / `summarize_results` with small DataFrames. Do **not** add live Athena tests.
- If you change `fetch_order_data` SQL, extend [`test_fetch_order_data_builds_exclusive_month_query`](test_data.py).
- `pytest` green. Do not break brand sectioning.

### P2 out of scope

P4, Streamlit, auth, replacing catalogs, per-brand colors (that is P3), rewriting the cron.

---

## P3 — Per-brand ambient color

One palette today (`--accent: #0f6a5a` in [`static/styles.css`](static/styles.css)). Histogram bars are hardcoded `#3d8f7f`. Coca-Cola / Monster / Ferrera pages should feel different (header, nav pills, buttons, metric cards, histogram) **without** a branding overhaul.

**Still blocked on Rick** for final brand examples. Do **not** wait forever: ship the `data-brand` hook and three variable blocks using the **starter** hex below so the pages are distinguishable. Rick can swap values in CSS later.

Starter (draft — replace if Rick provided swatches in the prompt):

| Brand slug | Accent | Soft / wash | Notes |
|---|---|---|---|
| `coca-cola` | `#c8102e` | `#fde8eb` | Coca-Cola red |
| `monster` | `#6abf4b` | `#e8f6e1` | Energy-drink green on the existing light gray UI |
| `ferrera` | `#8a5a2b` | `#f4ebe3` | Confection / gold-brown |

Keep `--bg`, `--surface`, `--ink`, `--danger` shared unless a swatch says otherwise.

### Implementation sketch

1. Set `data-brand="{{ brand }}"` on `<body>` in [`templates/base.html`](templates/base.html) (and catalog preview, which extends base). `brand_page_context` already passes `brand`.
2. In CSS: `:root` stays the current teal (home redirect is Coca-Cola; unknown/error pages can keep default). Then:

```css
body[data-brand="coca-cola"] { --accent: #c8102e; --accent-soft: #fde8eb; }
body[data-brand="monster"] { ... }
body[data-brand="ferrera"] { ... }
```

3. Point hardcoded teal at variables: `.bar-fill`, `.brand-nav a.active` border, `body` radial-gradient (use `color-mix` or a `--accent-wash` variable). Do not leave `#3d8f7f` / `#b7ddd3` / `rgba(15, 106, 90, …)` as the only brand look.
4. Keep layout and sectioning. No new screens. New brands (P4) should pick or inherit a theme later — optional `theme` field is **not** required now.

### P3 tests

- GET each of `coca-cola`, `monster`, `ferrera` → `data-brand="<slug>"` on the body.
- Sectioning tests still pass.
- Do not require visual snapshots.

### P3 out of scope

Custom brand-picker UI, uploading logos, dark mode, P4 new-brand flow.

---

## Shared constraints

- FastAPI + Jinja2 only.
- Flash stays on the session cookie; proposed points / rewards / catalogs stay in Postgres (P0/P1).
- `pytest` must pass without a real Railway DB or Athena.
- Smoke all three brand keys.
- Do not edit git Excel workbooks as live config.

---

## Suggested order

1. Land P1 on `main` if it is not there (rebase catalog branch onto `main`, PR, merge).
2. **P2:** document current inclusion in the PR; change Athena/simulator only if the product rule is not A.
3. **P3:** `data-brand` + CSS variables; starter palettes unless Rick supplied hex.
4. Run `pytest`, commit, push, open a PR (separate PRs for P2 vs P3 is fine; one PR is fine if both are small).

---

## Acceptance

**P2**
1. PR states the denominator in one sentence (A, B, or C) and whether `initial_quantity` stays.
2. If the rule did not change: caption and/or tests make current behavior obvious.
3. If the rule changed: website numbers match it; Athena cron still exits; `start.sh` roles unchanged.
4. Histogram still does not silently drop stores from Total Stores / reward counts.

**P3**
1. Opening Coca-Cola vs Monster vs Ferrera is visibly different (accent, nav, buttons, histogram).
2. Layout/sectioning unchanged.
3. `data-brand` present for all three slugs.

---

## Paste-ready agent prompt

```
Read HANDOFF.md and implement P2 and P3 only.

Baseline: origin/main. If P1 catalogs (brand_skus, catalog upload/download) are not on main yet, rebase origin/cursor/web-managed-catalogs-1268 onto main first (PR #7 merged into the P0 branch, not main; do not re-merge squash PR #6). Do not do P4. Do not change start.sh web vs refresh. Do not reintroduce Streamlit.

P2: Rick thinks stores without “enough” purchases may be dropped. Investigate fetch_order_data / get_month_orders / simulate / summarize_results first. There is no HAVING today. Document the denominator (current-month brand orderers vs ever-ordered vs all Mercaso stores) and whether SUM(initial_quantity) is correct. Change Athena/simulator only if the product rule is wrong. If current behavior is correct, add a results caption and unit tests that lock it in. No live Athena tests.

P3: Per-brand ambient color via data-brand on <body> and CSS variables. Keep layout/sectioning. Use HANDOFF.md starter hex unless Rick provided swatches. Replace hardcoded histogram/nav teal with variables.

pytest must pass without Railway/Athena. Smoke coca-cola, monster, ferrera. Commit, push, open a PR (one or two PRs).
```
