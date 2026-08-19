# M-Rewards Simulator

Streamlit app that simulates M-Rewards outcomes from store order history. Brand SKU lists and points live in Excel workbooks. Order snapshots live in **Railway Postgres**, refreshed monthly from AWS Athena.

The Streamlit app never queries Athena. Athena is a pull source used only by the monthly job.

## Data flow

1. On the 1st of each month, a Railway cron runs `python refresh_orders.py`.
2. The job queries Athena for the **previous complete month** (September 1 pulls August 1–31).
3. Those rows replace that month in Postgres (`orders` table). Earlier months stay put.
4. The simulator reads Postgres and shows a caption such as `Order data through August 2026`.

First run (empty `refresh_state`) backfills the last 6 complete months. Set `REFRESH_BACKFILL=1` to force a full backfill.

## Local development

```bash
pip install -r requirements.txt
export DATABASE_URL=postgresql://...
python refresh_orders.py          # requires AWS creds + Athena access
streamlit run rewards_simulator.py
```

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

### 2. Web service (Streamlit)

- Start command: `streamlit run rewards_simulator.py --server.port $PORT --server.address 0.0.0.0 --server.headless true --server.enableCORS false --server.enableXsrfProtection false`
- Env: `DATABASE_URL` only. No AWS keys.

`railway.toml` in this repo already sets that start command.

### 3. Cron service (monthly Athena pull)

- Start command: `python refresh_orders.py`
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

After the first successful deploy, trigger the cron service once (or run with `REFRESH_BACKFILL=1`) so Postgres is populated before anyone opens the app.

## Excel config

These workbooks stay in the repo. They are brand SKU/points config, not order history.

- `M-rewards-cocacola.xlsx`
- `M-rewards-monster.xlsx`
- `M-rewards-ferrera.xlsx`
