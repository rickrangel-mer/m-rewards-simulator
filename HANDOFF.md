# Handoff: Polish the UI and make brand pages feel like the brands (P6)

**Owner:** Rick (PM)  
**Repo:** `rickrangel-mer/m-rewards-simulator`  
**Product list:** [`backlog.md`](backlog.md)  
**This brief:** P6 only — visual and copy polish. Do not change auth, roles, simulator math, Athena, or `start.sh`. Do not reintroduce Streamlit. Do not add OAuth, SSO, or email sending. Do not download trademarked logos from the web.

Rick can attach logos, hex values, or brand PDFs on the implementing agent run. If nothing is attached, ship the look below with **typographic wordmarks** (not fake official logos).

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
| **P6** | This handoff |

**Start from `origin/main`.** The app is FastAPI + Jinja2 with login. Operators see every brand; suppliers see only assigned slugs. Brand pages already swap CSS variables via `data-theme` (`coca-cola`, `monster`, `ferrera`, `default`). That theming is a thin recolor of a generic gray tool. P6 is the visual product: it should look like a professional simulator **and** like Coca-Cola / Monster / Ferrera when you are on those pages.

---

## What this product is

Internal Mercaso M-Rewards simulator, now also shown to **suppliers**. Operators and suppliers pick a brand, a month of orders, SKU points, and reward cutoffs, and see how many stores would earn each reward.

A Coca-Cola supplier should feel they opened a Coca-Cola rewards tool, not a gray admin site with a red border. Same for Monster and Ferrera. Mercaso operators still need a calm, readable workspace — the brand personality cannot make tables or forms hard to use.

**Do not change the data path:** Postgres orders + catalogs, Athena cron, Pull order history, login/roles, New brand registry.

---

## P6 — A professional UI that caters to the main brands

### The problem today

- [`static/styles.css`](static/styles.css) lists `"IBM Plex Sans"` but never loads a font file, so the UI is system Segoe/sans.
- Palettes only retint `--accent`, `--cta`, topbar, and bars. Layout, type, cards, and login are the same on every brand.
- The topbar jams brand pills, New brand, email, Users, and Log out into a wrapping strip.
- The brand page is three stacked panels with similar visual weight. A new supplier cannot tell what to look at first.
- Login is a plain card. Users admin is a cramped table with nested forms.
- Metric tiles and the histogram work, but they look like a prototype.

### What “done” looks like

1. **Professional and easy to understand.** Clear hierarchy: where am I, what month, how many stores earned each reward, then the controls that change that outcome. Spacing, type, and labels should feel like a finished internal product, not a Form + table dump.
2. **New style, still the same app.** Same FastAPI routes, same POST forms, same Jinja templates (rewritten markup/CSS is expected). No React, no new SPA.
3. **Cater to the main brands.** Coca-Cola, Monster, and Ferrera each get a distinct visual system (color, type, header, surface treatment) keyed off existing `data-theme`. A screenshot of Coca-Cola vs Monster should be obviously different, not “same gray page, different pill color.”
4. **Default / login / Users stay Mercaso, not a random brand.** `/login`, `/users`, no-brands, and error pages use the **default** theme. A Coca-Cola supplier must not see Monster chrome on the sign-in screen.

### Information architecture (keep, clarify)

The brand page already has the right three blocks. Do not invent a fourth workflow. Make the existing ones obvious:

| Block | `id` (keep) | Job | Voice |
|---|---|---|---|
| Results | `simulation-results` | Answer: how many stores, which rewards | “This is the outcome for the selected month.” |
| SKU points | `sku-points` | The catalog + proposed points that drive the sim | “Change points, then update the simulation.” |
| Reward cutoffs | `reward-thresholds` | Names + point thresholds | “Stores need this many points to earn each reward.” |

Keep the month picker and **Pull order history** (operators) with results. Keep catalog upload/download and import/export with SKU points.

A short page header above the three blocks should show:

- Brand name (wordmark or provided logo)
- One-line purpose, e.g. “M-Rewards simulation”
- Order-data freshness (“Order data through July 2026”) — tests look for that phrase
- Selected month in plain language

Optional: a 1–2–3 hint (“Review results → adjust SKU points → set reward cutoffs”) that does not steal space on mobile.

### Visual system

**Load real fonts** (Google Fonts or self-hosted under `/static/fonts`). Pick a readable UI sans for body/tables (Inter, IBM Plex Sans, or Source Sans 3). Brand pages may swap a **display** font for the H1 / header wordmark only.

**Default (Mercaso) theme** — login, Users, error, no-brands, and brands that pick “Default teal” today:

- Replace leftover “generic teal admin” with a calm professional shell: deep ink, off-white canvas, white cards, one accent.
- Suggested tokens if Rick does not send Mercaso hexes: background `#f3f5f7`, ink `#121820`, accent `#1e4d7b` (navy, not the old mint). Update [`test_brand_theme_css_uses_variables_and_palettes`](test_app.py) if hexes change; that test currently forbids `#3d8f7f` and `circle at top left` from an earlier palette — do not revive that mint blob. A **subtle** brand-specific header treatment is allowed; a loud global gradient behind every table is not.

**Shared chrome:**

- Sticky header with: product name “M-Rewards”, brand switcher, operator actions, signed-in email + Log out.
- Brand switcher can stay pill/segmented nav; it should look designed, not like leftover links.
- Primary / secondary / danger buttons with consistent size and contrast.
- Cards with clear titles, one caption, then the control or table.
- Tables keep [`.data-table`](static/tables.js) sorting. Improve header/row spacing; do not break `th.sortable`.
- Flash messages and errors should match the theme (readable on Monster black headers too).
- Mobile: stacked header, full-width fields, no horizontal overflow in the topbar. Check a ~390px width.

**Login** (`templates/login.html`):

- Mercaso default theme only.
- Product name, one sentence (“Sign in with the email Mercaso assigned you”), email, password, Sign in.
- Keep the unconfigured-operator error copy (tests look for `operator account is not configured`) and generic invalid-password flash.

**Users** (`templates/users.html`):

- Same default theme. Make create-user vs account list two clear cards. Brand checkboxes as a compact chip/checkbox group. Keep POST targets (`/users`, `/users/{id}`, `/users/{id}/delete`).

**Catalog preview** and **New brand** dialog: same visual language as the brand page they sit on (preview inherits `data-theme`; dialog inherits the current page).

### Brand direction (the three mains)

Keep existing hex anchors unless Rick provides official values. Tests currently require `#f40009` (Coke), `#8dc63f` + `#111111` (Monster), `#e4007c` + `#5c2d91` + `#1e3fa8` (Ferrera). You may add more tokens; do not drop these without updating tests.

Use `body[data-theme="…"]` the way P3 does. `data-brand` stays the URL slug so a new brand can reuse a palette.

#### Coca-Cola (`coca-cola`)

Feel: classic soda — **red and white**, clean, confident, lots of white space. Ribbon or strong red rule in the header. Body stays light (white/off-white), not a red page of tables.

- Header: white or red with high-contrast wordmark “Coca-Cola” (or Rick’s logo).
- Display type: a tight sans or serif for the H1 only; body stays the UI sans.
- Accents: `#f40009` for primary buttons, active nav, histogram bars.
- Do **not** fake the Spencerian script logo. Do **not** scrape Coca-Cola artwork.

#### Monster (`monster`)

Feel: energy drink — **black chrome + lime**. Dark header is already right; push it so the page is unmistakably Monster without making SKU tables unreadable (table surface should stay light or high-contrast dark-gray with light text — pick one and commit).

- Lime `#8dc63f` for bars, active nav, highlights; black `#111111` for header/CTA.
- Display type: condensed/bold (e.g. Oswald or Barlow Condensed) for the brand title.
- Subtle texture OK (fine grid, not a claw watermark that fights numbers).
- Keep operator/supplier text readable (WCAG-ish contrast on lime-on-black).

#### Ferrera / Nerds (`ferrera`)

Feel: candy brand — **strawberry, grape, and Nerds blue**, playful but still a tool. Keep the two-color split already used on histogram bars.

- Pink `#e4007c`, purple `#5c2d91`, blue `#1e3fa8`.
- Light page, colorful header/pills/metric tiles — not a rainbow behind every row.
- Display type: slightly rounded sans for the H1 is OK; tables stay the UI sans.

#### New brands

They already pick a palette in the New brand dialog (`default` / `coca-cola` / `monster` / `ferrera`). P6 must still work for a slug like `pepsi` themed `coca-cola` or `default`. Do not hard-code only three URLs in CSS; keep `data-theme`.

### Logos (optional — Rick may attach)

If Rick attaches SVG/PNG files on the agent thread, put them in `/static/brand-marks/` (e.g. `coca-cola.svg`, `monster.svg`, `ferrera.svg`, `mercaso.svg`) and show them in the header/H1. If a file is missing, fall back to the styled wordmark. **Never** hotlink or copy official logos from the internet.

If Rick later sends files after P6 lands, a tiny follow-up PR is fine. Do not block the visual revamp on logos.

### Copy (make it easier; update tests if you change strings)

Clarity over jargon. Examples (not mandatory wording):

- Results caption already explains the store denominator — keep that meaning.
- SKU section: keep “Saved for everyone” so suppliers know edits are live.
- Buttons can stay “Update Simulation”, “Pull order history”, “New brand” (several tests assert these exact strings). Prefer keeping those labels unless you update every assertion in the same PR.

**Keep these ids and hooks** (tests and JS depend on them):

`simulation-results`, `sku-points`, `reward-thresholds`, `month-form`, `brand-refresh-form`, `catalog-upload`, `catalog-confirm`, `new-brand-dialog`, `new-brand-open`, `sim-form`, `data-brand`, `data-theme`, `class="data-table"`, `/static/tables.js`.

Nav links stay `/brands/{slug}` with the brand **label** as link text (Coca-Cola, Monster, Ferrera).

---

## Implementation notes

- FastAPI + Jinja2 + [`static/styles.css`](static/styles.css) (+ maybe a small `static/ui.js` if the header needs a user menu). No React, no CSS-in-JS.
- Prefer one stylesheet with `data-theme` blocks over per-brand CSS files, unless a file per theme is cleaner — both are OK.
- Do not change [`start.sh`](start.sh), [`refresh_orders.py`](refresh_orders.py), schema, bcrypt, or route auth. Operator-only New brand / Pull order history / Users stay operator-only. Forbidden brands stay **404**.
- Do not edit git Excel workbooks.
- [`test_brand_theme_css_uses_variables_and_palettes`](test_app.py) encodes the P3 hex contract and forbids the old mint blob. Update that test to match the new style **and** still prove the three palettes exist and the mint blob did not return.
- [`test_brand_page_sections_place_controls_with_outcomes`](test_app.py) and [`test_all_brands_render_sectioned_pages`](test_app.py) lock section order and many labels. If you rearrange DOM, keep the three section ids in that order (results, then SKUs, then rewards) or rewrite those tests in the same PR.
- Supplier isolation tests must still pass (Ferrera-only nav, no New brand, no Pull order history).
- Login and Users should get the same polish, not leftover prototype styling.
- Verify in the browser (or screenshots): login, Coca-Cola, Monster, Ferrera, Users, New brand dialog, catalog preview, a ~390px viewport. An operator session fixture already exists in tests.

---

## P6 tests

No live Railway or Athena. `pytest` green.

- Existing operator/supplier/auth/catalog/create-brand tests still pass (update assertions that pin old CSS/copy).
- Each of `coca-cola`, `monster`, `ferrera` still sets `data-theme` and is visually distinct in CSS (keep the hex anchors above unless Rick supplied new ones).
- Default/login CSS does not use a Coca-Cola or Monster header treatment.
- `.data-table` + sortable headers still work.
- Smoke: operator sees New brand + Pull order history; Ferrera-only supplier does not.

---

## P6 out of scope

Auth/roles, OAuth, SMTP, read-only suppliers, deleting brands, simulator/Athena logic, changing `start.sh`, Streamlit, React rewrite, a fourth git Excel workbook, scraping logos, a theme customizer for operators, dark/light OS toggle, new pages beyond restyling what exists.

---

## Shared constraints

- FastAPI + Jinja2 only.
- `pytest` must pass without a real Railway DB or Athena.
- Smoke `coca-cola`, `monster`, `ferrera` plus a supplier who can see only one of them.
- Do not edit git Excel workbooks as live config.

---

## Suggested order

1. Load fonts + rebuild default/Mercaso shell (topbar, buttons, cards, login, Users, error, no-brands).
2. Brand page header + clearer section hierarchy (keep ids).
3. Distinct Coca-Cola / Monster / Ferrera theme blocks (color, display type, header). Optional logos if attached.
4. Catalog preview + New brand dialog inherit the new chrome.
5. Update CSS/copy tests, `pytest`, browser-check the three brands + login, commit, push, open a PR.

---

## Acceptance

1. Login looks like a finished Mercaso product, not a default form.
2. On Coca-Cola, Monster, and Ferrera, a supplier can tell which brand they are in from the header and color alone.
3. Results, SKU points, and reward cutoffs are still the three blocks, easier to scan, with the same actions as today.
4. Operators still get New brand, Users, Pull order history; suppliers still do not. Brand isolation (404) unchanged.
5. `pytest` green; no Streamlit; `start.sh` unchanged.

---

## Attachments Rick may send the implementing agent

Anything here is optional. Ship without them using wordmarks + the hexes above.

- Official logos: Coca-Cola, Monster, Ferrera/Nerds, Mercaso (SVG preferred, PNG OK)
- Brand PDFs / hex / fonts Mercaso is allowed to use
- Mercaso primary color if navy `#1e4d7b` is wrong
- Preferred product title if not “M-Rewards Simulator”

---

## Paste-ready agent prompt

```
Read HANDOFF.md and implement P6 only.

Baseline: origin/main (P0–P5 landed, including login). Do not change start.sh, auth/roles, Athena, or simulator math. Do not reintroduce Streamlit. Do not add OAuth, SSO, or email. Do not download trademarked logos.

P6: revamp the UI so it looks professional and easy to understand. New visual style (fonts actually loaded, clearer hierarchy, polished topbar/cards/login/Users). Cater to the main brands via data-theme: Coca-Cola red/white, Monster black+lime, Ferrera pink/purple/blue — distinct headers/type, not a thin recolor. Login and Users stay default/Mercaso, not a brand theme. Keep section ids, form posts, operator-only controls, and 404 brand isolation. If Rick attached logos, use them from /static/brand-marks/; otherwise typographic wordmarks.

Update CSS/copy tests as needed. pytest must pass without Railway/Athena. Commit, push, open a PR.
```
