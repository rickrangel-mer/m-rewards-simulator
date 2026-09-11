# Handoff: Reward dollar values and a monthly/quarterly budget calculator (P7)

**Owner:** Rick (PM)  
**Repo:** `rickrangel-mer/m-rewards-simulator`  
**Product list:** [`backlog.md`](backlog.md)  
**This brief:** P7 only — monetary value on each reward, a budget calculator on the brand page, monthly **and** quarterly periods, and enough order history to reach **January 1** of the year. Do not reintroduce Streamlit. Do not change `start.sh` web vs `SERVICE_ROLE=refresh`. Do not add OAuth, SSO, or email sending. Do not invent a dollars-per-point SKU price unless Rick sends one.

---

## Baseline (read this before branching)

| Item | Status |
|---|---|
| **P0** proposed points + rewards in Postgres | **On `main`** — [PR #6](https://github.com/rickrangel-mer/m-rewards-simulator/pull/6) |
| **P1** web-managed SKU catalogs (`brand_skus`) | **On `main`** — [PR #9](https://github.com/rickrangel-mer/m-rewards-simulator/pull/9) |
| **P2** store-inclusion review | **On `main`** — [PR #9](https://github.com/rickrangel-mer/m-rewards-simulator/pull/9) |
| **P3** per-brand color | **On `main`** — palettes [PR #10](https://github.com/rickrangel-mer/m-rewards-simulator/pull/10), two-color split [PR #11](https://github.com/rickrangel-mer/m-rewards-simulator/pull/11) |
| **P4** New brand from a modal + registry | **On `main`** — [PR #13](https://github.com/rickrangel-mer/m-rewards-simulator/pull/13), slug/template [PR #14](https://github.com/rickrangel-mer/m-rewards-simulator/pull/14) |
| **P5** login + supplier brand access | **On `main`** — [PR #20](https://github.com/rickrangel-mer/m-rewards-simulator/pull/20) |
| **P6** UI polish + brand themes + wide workspace | **On `main`** — [PR #22](https://github.com/rickrangel-mer/m-rewards-simulator/pull/22), density [PR #23](https://github.com/rickrangel-mer/m-rewards-simulator/pull/23) |
| **P7** | This handoff |

**Start from `origin/main`.** FastAPI + Jinja2, login, supplier brand isolation. Brand page still simulates **one calendar month**: pick a month, SKU proposed points, reward **point cutoffs**, see how many stores earn each reward. Rewards have a name and a point threshold only — no dollar amount, no budget, no quarter.

Order snapshots live in Postgres (`orders`). Athena is pulled by the monthly cron and by operator **Pull order history**. Both currently backfill only the **last 6 complete months** ([`NUM_MONTHS = 6`](data.py)). On 1 Sep 2026 that is Mar–Aug 2026 — **not January**. Production often looks “capped at July” because the window never included Q1. P7 must pull from **1 January of the year of the last complete month** through that last complete month so quarterly views have the purchase data.

---

## What this product is

Internal Mercaso M-Rewards simulator, also shown to **suppliers** on assigned brands. Operators and suppliers set SKU points and reward cutoffs and see store outcomes.

Brands now need a **budget**: “If this many stores earn Reward 1, and Reward 1 is worth $8, what do we spend this month? This quarter?” That needs (1) a **dollar value on each reward**, (2) a **calculator section** that multiplies earners × value, (3) **SKU point totals** for the same period (units × proposed points — not dollars unless Rick adds a $ / point later), (4) a **monthly / quarterly** period control, and (5) **order rows back to January 1** so Q1 exists.

**Same store-inclusion rule as today** (P2): denominator is stores that ordered this brand in the **period** (month or quarter). `SUM(li.initial_quantity)` stays. Do not bring back `HAVING`.

---

## P7 — Monetary valuation + budget calculator + quarters

### 1. Dollar value on each reward

[`brand_rewards`](data.py) is `(brand, sort, name, points)`. Add a persisted **monetary value** per reward.

- Store as **integer cents** (`value_cents`, default `0`) so “8 Dollar Rebate” is `800`. Show dollars in the UI (`8` or `8.00`).
- Existing rows: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in [`init_schema()`](data.py) / [`SCHEMA_SQL`](data.py) the same way P0/P4 added tables. No manual Railway migrate.
- Reward cards on the brand page ([`#reward-thresholds`](templates/brand.html)): keep name + point cutoff; add a **Value ($)** field. POST with the existing simulate form (`sim-form`). Update [`parse_reward_form`](app.py), [`load_brand_rewards`](data.py), [`save_brand_rewards`](data.py), FakeStore, New brand dialog (optional value, default 0).
- Changing value must **not** change who earns the reward. Cutoffs stay points; value is only for the budget.

Keep current tests that assert `(name, points)` working by extending tuples to `(name, points, value_cents)` (or a small dict) in the same PR.

### 2. Budget calculator section (new block on the brand page)

Add a fourth section **after** reward cutoffs. Suggested `id="budget-calculator"` (tests should lock this id).

It answers: **what would this period cost, and how many points did the catalog issue?**

| Line | How to compute |
|---|---|
| **Per reward** | `stores earning that reward in the period` × `value_cents` |
| **Total budget** | Sum of per-reward lines |
| **Per SKU point totals** | For each catalog SKU: `units ordered in the period` × `proposed points` (fall back to current points like the sim). Show units + points issued. Not dollars. |

Use the **same** simulation as the results block for that period (same proposed points, same cutoffs, same inclusion). Do not invent a second scoring model.

Copy should say values are **estimated program cost** if every qualifying store is paid the reward’s dollar value. Saved-for-everyone still applies (same live `brand_rewards` rows).

Layout: match the P6 workspace (cards, pill buttons, `data-theme`). Do not dump a fourth identical table in a skinny column. A compact KPI row + two small tables (rewards $ / SKU points) is enough.

**Who sees it:** anyone who can open the brand page (operator or assigned supplier). Not operator-only.

### 3. Monthly vs quarterly period

Today the page is monthly only ([`#month-form`](templates/brand.html), `?month=2026-07`).

Add a **Month / Quarter** control that drives **both** simulation results **and** the budget (one period, not two competing pickers).

| Grain | Period | Simulation |
|---|---|---|
| **Monthly** (default) | One calendar month, as today | Existing [`get_month_orders`](simulator.py) + [`simulate`](simulator.py) |
| **Quarterly** | The calendar quarter that contains the selected month (Q1=Jan–Mar, Q2=Apr–Jun, Q3=Jul–Sep, Q4=Oct–Dec) | Concatenate that quarter’s complete months (same SKU filter), then **one** `simulate()` on the combined units/points. A store that ordered the brand in any month of the quarter is in the denominator. Thresholds apply to **quarter totals**, not three separate monthly payouts. |

Keep `?month=` so existing tests still work. Add something like `?grain=month|quarter` (default `month`). When `grain=quarter`, label the period in plain language (“Q3 2026”). If the quarter is not finished (e.g. viewing Q3 on 11 Sep 2026), use **complete months only** and say so (July–August of Q3, not a fake September).

Optional: a quarter dropdown instead of deriving from month — either is fine if the URL is testable and monthly default is unchanged.

Histogram / “Total Stores” / reward counts in `#simulation-results` must follow the same period. Caption that says “this month” must be honest when the grain is quarter (update tests that pin “Using July 2026 ordering data” if you change that string; keep the meaning).

### 4. Order history from January 1 (Athena window)

The Athena SQL is already parameterized by `start`/`end`. The **cap is Python**, not a July literal:

- [`NUM_MONTHS = 6`](data.py)
- [`backfill_windows()`](data.py)
- Cron first run + [`REFRESH_BACKFILL`](refresh_orders.py)
- Operator **Pull order history** ([`run_brand_refresh`](refresh_orders.py) always uses `backfill_windows`)

**Change:** windows for backfill / Pull order history must cover every **complete** calendar month from **January 1 of the year of the last complete month** through that last complete month.

Examples:

- Today 11 Sep 2026 → last complete month Aug 2026 → windows **Jan 2026 … Aug 2026** (not Mar–Aug).
- Today 15 Jan 2027 → last complete month Dec 2026 → windows **Jan 2026 … Dec 2026**.

Monthly cron **after** `refresh_state` is populated still pulls **only the previous complete month** (do not re-Athena the whole year every 1st). Empty `refresh_state`, `REFRESH_BACKFILL=1`, and **Pull order history** use the January–through–last-complete-month span.

After deploy, operators should **Pull order history** (or one `REFRESH_BACKFILL=1` cron) so Railway actually contains Q1. The website cannot invent months that are not in `orders`.

Update [`test_backfill_windows_are_six_complete_months_oldest_first`](test_data.py) and any refresh tests that assume six windows. [`rewards_analysis.py`](rewards_analysis.py) divides by `NUM_MONTHS`; if you keep that script, point it at the new helper or leave it as optional analysis — do not break `pytest`.

Do not change `start.sh`. Do not query Athena in unit tests.

---

## Implementation notes

- FastAPI + Jinja2 only. No React. Same session/auth. Forbidden brands stay **404**. New brand / Pull order history / Users stay **operator-only**. Budget is visible to suppliers on assigned brands.
- Keep section ids that tests use: `simulation-results`, `sku-points`, `reward-thresholds`, `month-form`, `brand-refresh-form`, `catalog-upload`, `sim-form`, `data-table`. Add `id="budget-calculator"`. Keep DOM order: results, SKUs, rewards, **then** budget (or rewrite [`test_brand_page_sections_place_controls_with_outcomes`](test_app.py) in the same PR).
- Prefer a small pure helper next to [`simulate`](simulator.py) (e.g. `orders_for_period`, `budget_from_results`) so FakeStore tests do not need Postgres.
- Reward POST field names: keep `reward_name` / `reward_points`; add e.g. `reward_value` (dollars in the form, cents in Postgres).
- Default 0 value is OK. Do not fail simulate if every value is 0 — budget just shows $0.
- Do not edit git Excel workbooks.
- README: document the longer Athena backfill (Jan 1 → last complete month) and that Pull order history is how existing Railway DBs catch up.

---

## P7 tests

No live Railway or Athena. `pytest` green.

- Reward value round-trips: set $8 on a reward, reload, still $8; FakeStore / persist_store updated.
- Budget: with two stores earning a $8 reward, total shows **$16** (or 1600 cents formatted). SKU point totals match units × proposed points for the period.
- `grain=month` (default): same as today’s monthly sim (`?month=2026-07` still works).
- `grain=quarter`: Q3 2026 with July+August fixture orders scores **combined** units/points; a store that only ordered in July still counts in the quarter denominator.
- Backfill windows on 1 Sep 2026 start at **2026-01-01**, not 2026-03-01. Pull-order-history / first cron backfill uses that span. Incremental cron still one month.
- Existing catalog / simulate / create-brand / supplier isolation tests still pass (update tuple shapes / copy as needed).
- Operator still sees New brand + Pull order history; Ferrera-only supplier does not, but **does** see the budget section on Ferrera.

---

## P7 out of scope

OAuth / SSO / email, read-only suppliers, deleting brands, Streamlit, React, a fourth git Excel workbook, dollars-per-point SKU costing, multi-currency, paying stores in the real world, changing the P2 inclusion rule, widening Athena to years before the current Jan 1, changing `start.sh` roles.

If Rick later wants “sum of three monthly payouts” instead of one quarterly cumulative score, that is a follow-up. This brief is **one simulate() per selected period**.

---

## Shared constraints

- FastAPI + Jinja2 only.
- `pytest` must pass without a real Railway DB or Athena.
- Smoke `coca-cola`, `monster`, `ferrera` plus a supplier who can see only one of them.
- Do not edit git Excel workbooks as live config.

---

## Suggested order

1. `value_cents` on `brand_rewards` + form/FakeStore; existing reward tests green.
2. January-1 backfill helper; update window tests; Pull order history / first cron use it; incremental cron unchanged.
3. Period grain (month default / quarter) wired through `get_month_orders` / a quarter combiner; results captions honest.
4. `#budget-calculator`: earners × $, SKU point totals; suppliers can see it.
5. README, `pytest`, browser-check monthly vs quarterly + a reward value, commit, push, open a PR.

---

## Acceptance

1. Each reward has a dollar value, saved for everyone like cutoffs.
2. The brand page has a budget block: reward $ totals and SKU point totals for the selected period.
3. User can view **month** or **quarter**; simulation results and budget use the same period.
4. Athena/backfill/Pull order history can load complete months from **January 1** of that year (not a rolling 6-month cap). Incremental monthly cron still one month.
5. `pytest` green; no Streamlit; `start.sh` roles unchanged; suppliers still 404 on other brands.

---

## Attachments Rick may send the implementing agent

Optional. Ship with $0 defaults if missing.

- Official $ amounts for Coca-Cola / Monster / Ferrera reward names
- Confirm quarterly = **cumulative quarter** (this brief) vs sum of monthly payouts
- Confirm YTD start is calendar **January 1** of the last complete month’s year

---

## Paste-ready agent prompt

```
Read HANDOFF.md and implement P7 only.

Baseline: origin/main (P0–P6 landed, including login and the UI revamp). Do not change start.sh web vs SERVICE_ROLE=refresh. Do not reintroduce Streamlit. Do not add OAuth, SSO, or email.

P7: add a dollar value on each reward (persist cents on brand_rewards). Add a budget calculator section on the brand page: stores earning each reward × $ value, plus SKU point totals (units × proposed points). One period control: monthly (default, keep ?month=) or quarterly (calendar quarter, one simulate() on combined complete months). Same store-inclusion rule as today.

Extend Athena backfill / Pull order history windows from January 1 of the last complete month’s year through that month (not rolling 6 months). Incremental cron still pulls only the previous complete month.

Keep auth, 404 brand isolation, operator-only New brand / Pull order history / Users. pytest must pass without Railway/Athena. Commit, push, open a PR.
```
