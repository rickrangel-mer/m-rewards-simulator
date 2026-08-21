# Product backlog

Priority **0** is highest (do next). **5** is lowest.

Brand page sectioning (month selector with results; SKU tools with the editor; reward controls with thresholds) shipped in [PR #3](https://github.com/rickrangel-mer/m-rewards-simulator/pull/3). FastAPI + Jinja2; no Streamlit. Web never talks to Athena.

**Direction:** Excel stays the *bulk edit format* (upload/download). It should not stay the *live config*. Postgres should own brand SKUs, points, and rewards; the three git workbooks (`M-rewards-cocacola.xlsx`, `M-rewards-monster.xlsx`, `M-rewards-ferrera.xlsx`) become a one-time seed, then leave runtime.

**Next:** P4 (new brand from a template). P0–P3 are in this stack: catalogs rebase onto `main`, store-inclusion review, per-brand color.

| P | Item | Notes |
|---|---|---|
| **0** | **Persist proposed points, reward names, and thresholds** | **On `main`.** [PR #6](https://github.com/rickrangel-mer/m-rewards-simulator/pull/6). Working edits in `brand_proposed_points` / `brand_rewards`. |
| **1** | **Replace git Excel files with web-managed catalogs** | **This PR (rebase onto `main`).** [PR #7](https://github.com/rickrangel-mer/m-rewards-simulator/pull/7) targeted the P0 branch after `#6` was squash-merged. Download/upload Excel, store in Postgres (`brand_skus`), website and Athena refresh read that table. Git workbooks seed empty catalogs only. Canonical columns: `sku`, `product_title`, `current_points`. |
| **2** | **Review store-inclusion logic (Athena + simulator)** | **This PR.** Denominator is **A — current-month brand orderers**. No `HAVING` / min-quantity filter. `SUM(li.initial_quantity)` stays (no cancelled/net-qty predicate today). Caption + tests lock the rule; Athena SQL unchanged. |
| **3** | **Per-brand ambient color** | **This PR.** `data-brand` on `<body>` + CSS variables. Starter hex: Coca-Cola `#c8102e`, Monster `#6abf4b`, Ferrera `#8a5a2b`. Layout/sectioning unchanged. |
| **4** | **Create a new brand page from a template** | **Next.** Display name + URL slug, upload the canonical Excel, at least one reward name + cutoff. Nav from the Postgres registry. Optional accent from the P3 palettes. New SKUs only appear in simulations after an Athena refresh (or the 1st-of-month cron). |
