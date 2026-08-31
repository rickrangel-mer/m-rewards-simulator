# M-Rewards Simulator

FastAPI + Jinja2 web app that simulates M-Rewards outcomes from store order history. Brand SKU catalogs (which SKUs exist, titles, current points) live in **Railway Postgres**, seeded once from the git Excel workbooks. Order snapshots also live in Postgres, refreshed monthly from AWS Athena.

The website reads Postgres. The monthly cron pulls Athena into `orders`. Operators can also **Pull order history** on a brand page to query Athena for that brand's SKUs only (last 6 complete months) without waiting for the 1st-of-month job.

## Data flow

1. On the 1st of each month, a Railway cron runs `python refresh_orders.py`.
2. The job queries Athena for the **previous complete month** (September 1 pulls August 1–31).
3. Those rows replace that month in Postgres (`orders` table). Earlier months stay put.
4. The website reads Postgres and shows a caption such as `Order data through August 2026`.

First run (empty `refresh_state`) backfills the last 6 complete months. Set `REFRESH_BACKFILL=1` to force a full backfill.

## Local development

```bash
pip install -r requirements.txt
export DATABASE_URL=postgresql://...
export SESSION_SECRET=dev-secret-change-me
export OPERATOR_EMAIL=you@mercaso.com
export OPERATOR_PASSWORD=choose-a-password
python refresh_orders.py          # requires AWS creds + Athena access
uvicorn app:app --reload --port 8080
```

`OPERATOR_EMAIL` / `OPERATOR_PASSWORD` create the first operator only when `users` is empty. `pytest` does not need Railway or Athena.

Optional local analysis (reads Postgres, writes gitignored CSV dumps):

```bash
python rewards_analysis.py
```

```bash
pytest
```

## Railway setup

Create one project with **Postgres** plus **two services** from this repo.

### 1. Postgres

Add Railway Postgres. Both services should receive `DATABASE_URL` (Railway does this when you link the plugin).

### 2. Web service (FastAPI)

- Start command comes from `railway.toml` (`sh start.sh` → FastAPI/uvicorn by default).
- Env: `DATABASE_URL`. Do **not** set `SERVICE_ROLE`.
- Required: `SESSION_SECRET` (a long random string) for signed login cookies.
- First deploy only: `OPERATOR_EMAIL` and `OPERATOR_PASSWORD`. If the `users` table is empty, the web app creates that operator on boot. It does **not** reset an existing operator on later deploys. Copy the password into a password manager; there is no reset email.
- Optional (needed for **Pull order history** on a brand page): the same Athena AWS vars as the cron service (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`, `ATHENA_S3_STAGING`). The monthly cron still refreshes every catalog SKU; the button pulls only that brand's SKUs. **Pull order history** and **New brand** are operator-only.

The site is not public. Unauthenticated visitors are sent to `/login`. Operators see every brand plus a **Users** page to create suppliers and assign brand slugs. Suppliers only see the brands assigned to them; other brand URLs return 404.

Generate a public domain on the web service (Settings → Networking).

### 3. Cron service (monthly Athena pull)

- Same repo and same start command (`sh start.sh`), but set:

```
SERVICE_ROLE=refresh
```

That makes `start.sh` run `python refresh_orders.py` instead of the website.

- Settings → Cron Schedule: `0 6 1 * *` (06:00 UTC on the 1st)
- The process must exit when finished (this script does).

Env vars (cron service only):

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Railway Postgres |
| `AWS_ACCESS_KEY_ID` | Athena query |
| `AWS_SECRET_ACCESS_KEY` | Athena query |
| `AWS_DEFAULT_REGION` | `us-west-2` |
| `ATHENA_S3_STAGING` | `s3://mercaso-data-platform-prod/athena/sql/` |
| `REFRESH_BACKFILL` | Set to `1` for a one-off 6-month backfill |
| `SERVICE_ROLE` | Must be `refresh` |

After the first successful deploy, trigger the cron service once (or run with `REFRESH_BACKFILL=1`) so Postgres is populated before anyone opens the app.

## Excel config

The three git workbooks seed Postgres **once** (empty `brand_skus` for that brand). After that, the website catalog is the source of truth for the web app and the Athena refresh SKU list.

- `M-rewards-cocacola.xlsx`
- `M-rewards-monster.xlsx`
- `M-rewards-ferrera.xlsx`

Download the current catalog as Excel from a brand page, edit `sku`, `product_title`, `current_points` (optional: `size`, `brand`, `category`), then upload and preview. **Merge** keeps SKUs that are not in the file. **Replace** makes the file the full list. Do not treat the git workbooks as live config.
