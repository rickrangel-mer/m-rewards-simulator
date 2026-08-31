# Handoff: Login and supplier brand access (P5)

**Owner:** Rick (PM)  
**Repo:** `rickrangel-mer/m-rewards-simulator`  
**Product list:** [`backlog.md`](backlog.md)  
**This brief:** P5 only. Do not reintroduce Streamlit. Do not change `start.sh` web vs `SERVICE_ROLE=refresh`. Do not add logos. Do not add OAuth, SSO, or email sending.

---

## Baseline (read this before branching)

| Item | Status |
|---|---|
| **P0** proposed points + rewards in Postgres | **On `main`** — [PR #6](https://github.com/rickrangel-mer/m-rewards-simulator/pull/6) |
| **P1** web-managed SKU catalogs (`brand_skus`) | **On `main`** — landed via [PR #9](https://github.com/rickrangel-mer/m-rewards-simulator/pull/9) |
| **P2** store-inclusion review | **On `main`** — [PR #9](https://github.com/rickrangel-mer/m-rewards-simulator/pull/9) |
| **P3** per-brand color | **On `main`** — [PR #9](https://github.com/rickrangel-mer/m-rewards-simulator/pull/9), palettes [PR #10](https://github.com/rickrangel-mer/m-rewards-simulator/pull/10), two-color split [PR #11](https://github.com/rickrangel-mer/m-rewards-simulator/pull/11) |
| **P4** New brand from a modal + registry | **On `main`** — [PR #13](https://github.com/rickrangel-mer/m-rewards-simulator/pull/13), slug/template [PR #14](https://github.com/rickrangel-mer/m-rewards-simulator/pull/14) |
| **Follow-ups on `main`** | Uncap points [#15](https://github.com/rickrangel-mer/m-rewards-simulator/pull/15), import 422 [#16](https://github.com/rickrangel-mer/m-rewards-simulator/pull/16), **Pull order history** [#17](https://github.com/rickrangel-mer/m-rewards-simulator/pull/17), sortable tables [#18](https://github.com/rickrangel-mer/m-rewards-simulator/pull/18) |
| **P5** | This handoff |

**Start from `origin/main`.** The website is a public FastAPI app with **no login**. Anyone with the Railway URL can open every brand, edit proposed points, replace catalogs, create brands, and (if AWS env is on the web service) pull Athena. That is the P5 problem.

Session cookies already exist ([`SessionMiddleware`](app.py)) for flash messages only. `SESSION_SECRET` is optional today.

---

## What this product is

Internal Mercaso M-Rewards simulator. Operators pick a brand, a month of orders, SKU points, and reward cutoffs, and see how many stores would earn each reward.

Rick now wants to **share the simulator with some suppliers**. A supplier must **only see the brand(s) Mercaso assigns to them**. A Coca-Cola supplier must not see Monster or Ferrera in the nav, and must not open those pages by URL.

**Data path (unchanged):**
- Orders in Railway Postgres (`orders`, `refresh_state`)
- Monthly Athena cron (`SERVICE_ROLE=refresh` → `refresh_orders.py`)
- Brand page **Pull order history** ([PR #17](https://github.com/rickrangel-mer/m-rewards-simulator/pull/17)) queries Athena for **that brand’s SKUs only**
- FastAPI + Jinja2; sectioned brand page; catalogs in `brand_skus`; nav from the `brands` registry

---

## P5 — Login and roles so suppliers only see assigned brands

Two roles:

| Role | Who | What they see | What they can do |
|---|---|---|---|
| **operator** | Mercaso | Every brand in the registry | Current full app: brand pages, New brand, catalog merge/replace, Pull order history, plus a **Users** page to create suppliers and assign brands |
| **supplier** | External partner | **Only assigned brand slug(s)** | Use that brand’s page (simulate, proposed points, rewards, catalog upload/download for **that brand**). No New brand. No Users. No Pull order history. Direct URLs to other brands act like unknown brand (**404**, do not say “you cannot access Monster”). |

One supplier can be assigned **one or more** brands (a candy supplier might get Ferrera only; later they might get a second slug). Operators do not need a brand list; they see all.

**Same Postgres rows.** Supplier edits to Coca-Cola proposed points / rewards / catalog are the live brand data, same as an operator editing Coca-Cola. There are no per-user drafts.

### Auth model (keep it small)

Email + password in Postgres. No Google login, no Auth0, no magic links, no SMTP.

| Table | Purpose |
|---|---|
| `users` | `id`, `email` (unique, stored lowercase), `password_hash`, `role` (`operator` \| `supplier`), `created_at` |
| `user_brands` | `(user_id, brand)` where `brand` is a registry slug. Operators ignore this table. |

Hash passwords with **bcrypt** (add a dependency). Do not store plaintext. Do not log passwords.

**Bootstrap:** if `users` is empty and `OPERATOR_EMAIL` + `OPERATOR_PASSWORD` are set, `ensure_schema()` (or first web boot) creates that operator. If the table is empty and those env vars are missing, the login page must say the operator account is not configured — **do not leave the app open unauthenticated**.

Document the new env vars in [`README.md`](README.md) under the web service:

- `SESSION_SECRET` — required for signed cookies (stop using the hardcoded default once login exists)
- `OPERATOR_EMAIL` / `OPERATOR_PASSWORD` — first operator only (create-if-empty; do not reset an existing operator on every boot)

Operator creates supplier accounts in the UI and copies the password to the supplier. No “forgot password” email.

### Login UX

- `/login` — email + password. Unauthenticated visitors of any other HTML page redirect here (keep `?next=` only if it is a same-origin path).
- Successful login: session stores user id (not the password). Redirect to `/` or `next`.
- `/` today always goes to Coca-Cola. Change it to the **first assigned brand** (operators: first registry brand, same as today). Supplier with no brands: a simple “No brands assigned” page, not Coca-Cola.
- Logout in the topbar. Clears the session.
- Show the signed-in email in the topbar. Operators also get a **Users** link. Hide **New brand** for suppliers.

Allow without a session: `GET /health`, `/login`, `/static/*`. Everything else, including `/catalog-template.xlsx` and every `/brands/...` GET/POST, requires a session.

### Enforce on the server

Nav filtering is not enough. Check the current user on **every** brand route, including POST:

- [`GET /brands/{brand}`](app.py)
- `POST /brands/{brand}/simulate`
- `POST /brands/{brand}/import`
- `GET /brands/{brand}/export`
- `GET /brands/{brand}/catalog.xlsx`
- `POST /brands/{brand}/catalog` and `/catalog/confirm`
- `POST /brands/{brand}/refresh-orders` — **operators only** (Athena). Suppliers get 403/404 even on a brand they can view.
- `POST /brands/create` and the New brand dialog — **operators only**
- Users admin routes — **operators only**

Put the helper next to [`page_chrome()`](app.py): `allowed_brands(user)` and `can_access_brand(user, slug)`. [`page_chrome()`](app.py) must list **only allowed brands** so suppliers never get other labels in the nav. [`templates/error.html`](templates/error.html) “Back to simulator” must not send a supplier to Coca-Cola if they cannot see it.

### Users admin (operators)

A small Jinja page, same FastAPI style. Not a separate app.

- List users (email, role, assigned brands)
- Create supplier: email, password, one or more brand checkboxes from the registry
- Edit assignments (add/remove brand slugs)
- Optional: delete or set a new password for a supplier
- Do not allow a supplier to create users
- Creating a second operator is OK (same form, role select) so Mercaso is not a single account

Validate that assigned slugs exist in `brands`.

---

## Implementation notes

- FastAPI + Jinja2 only. A login template + users template. A few lines of existing session JS patterns is fine; no React.
- Middleware or a shared dependency is better than copying `if not user` into every route. Tests need one **autouse fixture** that signs in an operator (or patches `current_user`) so existing [`test_app.py`](test_app.py) cases keep working.
- Extend [`SCHEMA_SQL`](data.py) / `ensure_schema()` the same way P0/P4 added tables. No manual Railway migrate.
- Cron / `refresh_orders.py` has no HTTP users. Do not add login there.
- FakeStore in tests should grow `users` / `user_brands` (or a sibling fake) so supplier tests do not need Postgres.
- `SESSION_SECRET`: fail closed in production if unset, or keep the default **only** when `OPERATOR_*` is unset **and** this is pytest. Prefer: tests set `SESSION_SECRET=test`.
- Overlapping SKUs (Coca-Cola’s catalog includes some Monster drinks) stay as they are. P5 isolates **brand pages**, not SKU rows inside a catalog.

---

## P5 tests

No live Railway or Athena.

- Unauthenticated `GET /brands/coca-cola` → redirect to `/login` (not 200).
- Operator session: Coca-Cola, Monster, Ferrera, New brand, Pull order history still work.
- Supplier assigned `ferrera` only: nav has Ferrera, not Coca-Cola/Monster; `GET /brands/ferrera` is 200; `GET /brands/coca-cola` is 404; `POST /brands/create` is denied; `POST /brands/ferrera/refresh-orders` is denied.
- Supplier with two brands sees both in nav.
- Supplier with zero brands does not land on Coca-Cola.
- Login with wrong password stays on `/login` with a flash. Do not reveal whether the email exists.
- Existing FakeStore catalog / simulate / create-brand tests still pass via the operator autouse fixture.
- `pytest` green.

---

## P5 out of scope

OAuth / SSO / Google login, magic links, SMTP, 2FA, “forgot password” email, per-SKU or per-store permissions, hiding SKUs that appear on another brand’s catalog, read-only suppliers, deleting brands, logos, Streamlit, changing `start.sh`, a fourth git Excel workbook.

If Rick later wants suppliers **read-only** (view simulation, no point/catalog edits), that is a follow-up. This brief is **visibility**: they may use the assigned brand page, they may not see other brands.

---

## Shared constraints

- FastAPI + Jinja2 only.
- `pytest` must pass without a real Railway DB or Athena.
- Smoke `coca-cola`, `monster`, `ferrera` plus a supplier who can see only one of them.
- Do not edit git Excel workbooks as live config.

---

## Suggested order

1. `users` + `user_brands` schema, bcrypt, seed first operator from env.
2. `/login`, `/logout`, session user, redirect unauthenticated HTML to login. `/health` stays public.
3. Filter nav + 404 other brands. `/` → first allowed brand.
4. Operator-only: New brand, Pull order history, Users admin (create supplier, assign brands).
5. Tests (autouse operator + new supplier isolation cases), README env vars, `pytest`, commit, push, open a PR.

---

## Acceptance

1. With the Railway URL, a logged-out visitor cannot open a brand page.
2. An operator still sees every brand, can create brands, and can pull order history.
3. A supplier assigned only Ferrera sees Ferrera and cannot open Coca-Cola or Monster by clicking or by typing the URL.
4. Mercaso can add a supplier (email, password, brand checkboxes) without a code change.
5. No Streamlit; `start.sh` roles unchanged; cron still has no login.

---

## Paste-ready agent prompt

```
Read HANDOFF.md and implement P5 only.

Baseline: origin/main. P0–P4 are on main (including New brand registry and Pull order history). Do not change start.sh web vs SERVICE_ROLE=refresh. Do not reintroduce Streamlit. Do not add OAuth, SSO, email, or logos.

P5: the site is public today. Add email/password login (bcrypt, Postgres users + user_brands). Roles: operator (all brands, current app + Users admin) and supplier (only assigned brand slugs). Nav and every /brands/{brand} GET/POST must enforce that. Unknown/forbidden brands are 404, not a leaky 403. / goes to the first allowed brand. New brand and Pull order history are operator-only. Bootstrap the first operator from OPERATOR_EMAIL + OPERATOR_PASSWORD when users is empty. SESSION_SECRET for cookies.

Keep existing tests green with an autouse operator session fixture. Add supplier isolation tests. pytest must pass without Railway/Athena. Commit, push, open a PR.
```
