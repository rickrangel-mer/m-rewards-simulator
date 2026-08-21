# Handoff: M-Rewards website UX optimization

**Owner:** Rick (PM)  
**Repo:** `rickrangel-mer/m-rewards-simulator`  
**Branch baseline:** `main` (FastAPI site live; Streamlit removed)  
**Next focus:** Page sectioning so controls sit with the outcomes they affect

---

## What this product is

Internal simulator for Mercaso M-Rewards. For a brand (Coca-Cola / Monster / Ferrera), pick a month of historical orders, set points per SKU and reward thresholds, and see how many stores would earn each reward.

**Data:**
- Order snapshots in Railway Postgres (`orders`, `refresh_state`)
- Monthly Athena pull via cron (`SERVICE_ROLE=refresh` → `refresh_orders.py`)
- Brand SKU/points config in Excel workbooks in the repo
- Web app never talks to Athena

---

## What the “Points Distribution” graph is

It is a **histogram of total points earned per store** for the selected simulation month.

- **X-axis (implicit):** buckets of store total points (e.g. 0–200, 200–400, …)
- **Y-axis (bar height):** how many stores fall in that bucket
- Built in `summarize_results()` in [`simulator.py`](simulator.py): takes each store’s `total_points`, clips the top 1% so outliers don’t squash the chart, bins into ~10–40 buckets, counts stores per bucket
- Rendered as CSS bars in [`templates/brand.html`](templates/brand.html)

**How to read it:** most stores cluster where the bars are tall. Reward thresholds cut that distribution — the “Stores earning each reward” cards answer “how many clear this bar?”; the histogram shows the shape of the whole population.

**Gaps today:** no axis labels, no numeric tick marks, no vertical lines for reward thresholds (Streamlit Altair chart used to draw threshold rules). Improving the chart is optional; sectioning is the priority below.

---

## Current page layout (problem)

[`templates/brand.html`](templates/brand.html) is one long page with controls in the wrong mental model:

1. **Simulation results** (metrics, reward counts, histogram, store table) — at top
2. Import / export — floating above the editor
3. **One big form** mixing:
   - Month selector + search + bulk points (same control row)
   - SKU point editor
   - Reward thresholds
   - “Update Simulation”

**UX issue Rick wants fixed:** buttons and selectors feel attached to the wrong section. Example: the **month selector** changes which order month feeds the **simulation results**, but it sits with the **point editor** tools (search / bulk apply).

---

## Target sectioning (next agent)

Reorganize so each section has one job and only the controls that belong to it.

Suggested structure:

### Section A — Simulation context & results
**Purpose:** Pick what month to simulate and see outcomes.

**Controls that belong here:**
- Simulation month selector
- (Optional) short caption: “Using July 2026 ordering data”
- Update / refresh results if month change is not auto-submit

**Content:**
- Total / avg / median / max points
- Stores earning each reward (count + %)
- Points distribution histogram
- Store-level detail

### Section B — SKU points
**Purpose:** Edit points that drive the simulation.

**Controls that belong here:**
- Search / filter SKUs
- Bulk point value + Apply to selected
- Import proposed points
- Export SKU CSV
- SKU table (current + proposed points)

### Section C — Reward thresholds
**Purpose:** Define reward names and point cutoffs.

**Controls that belong here:**
- Reward name / points fields
- Add reward / Remove reward

### Global
- Brand nav (Coca-Cola / Monster / Ferrera) stays in the top bar
- Freshness caption (“Order data through …”) stays in header

**Principle:** If a control only changes results for a given month, put it in Section A. If it only edits SKU points, put it in Section B. Avoid one mega-toolbar that mixes month + search + bulk.

---

## Key files for the next agent

| File | Role |
|------|------|
| [`templates/brand.html`](templates/brand.html) | Main brand page layout — primary edit surface |
| [`templates/base.html`](templates/base.html) | Shell, brand nav, freshness |
| [`static/styles.css`](static/styles.css) | Layout / section styling |
| [`app.py`](app.py) | Routes, form actions, session proposed points / rewards |
| [`simulator.py`](simulator.py) | Pure sim helpers + `summarize_results` / histogram |
| [`data.py`](data.py) | Postgres + Excel loaders |
| [`start.sh`](start.sh) | Web = uvicorn; cron = `SERVICE_ROLE=refresh` |

Simulation already runs on **GET** page load (`brand_page_context` → `run_brand_simulation`). Month change currently GET-submits via JS on the select. After sectioning, keep that behavior (or equivalent) so results stay in sync with the month control in Section A.

---

## Out of scope for this handoff (unless Rick expands)

- Changing Athena / Postgres / cron
- Reintroducing Streamlit
- Full React rewrite (stack is FastAPI + Jinja2 by choice)
- Branding overhaul beyond section clarity

---

## Acceptance criteria for the sectioning work

1. Month selector is visually and structurally inside the **results** section, not the SKU editor.
2. Search, bulk apply, import, export sit with the **SKU point editor**.
3. Add/remove reward controls sit with **reward thresholds**.
4. Existing behavior preserved: page-load results, reward store counts, proposed points session, import/export, brand switch.
5. Mobile-usable: sections stack cleanly; no broken forms after splitting HTML forms if needed (may need multiple forms + hidden fields or small JS to sync month into other posts).

---

## Suggested first steps for next agent

1. Sketch Section A / B / C in `brand.html` with clear headings.
2. Move month `<select>` into the results panel; keep auto-refresh on change.
3. Move search / bulk / import / export into the SKU panel only.
4. Split or nest forms carefully so POST actions still receive `month`, `sku`, `proposed_points`, and reward fields.
5. Smoke-test all three brands on Railway after deploy.

---

## Deploy reminder

After UI changes: push to `main` (or PR), then **redeploy the Railway web service**. Cron/refresh service unchanged unless `start.sh` / env vars change.
