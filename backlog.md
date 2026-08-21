# Product backlog

Priority **0** is highest (do next). **5** is lowest.

Brand page sectioning (month selector with results; SKU tools with the editor; reward controls with thresholds) shipped in [PR #3](https://github.com/rickrangel-mer/m-rewards-simulator/pull/3). FastAPI + Jinja2; no Streamlit. Web never talks to Athena.

**Direction:** Excel stays the *bulk edit format* (upload/download). It should not stay the *live config*. Postgres should own brand SKUs, points, and rewards; the three git workbooks (`M-rewards-cocacola.xlsx`, `M-rewards-monster.xlsx`, `M-rewards-ferrera.xlsx`) become a one-time seed, then leave runtime.

**Next:** P2 + P3. Spec + paste-ready prompt: [`HANDOFF.md`](HANDOFF.md).

| P | Item | Notes |
|---|---|---|
| **0** | **Persist proposed points, reward names, and thresholds** | **On `main`.** [PR #6](https://github.com/rickrangel-mer/m-rewards-simulator/pull/6). Working edits in `brand_proposed_points` / `brand_rewards`. |
| **1** | **Replace git Excel files with web-managed catalogs** | **Merged off-main.** [PR #7](https://github.com/rickrangel-mer/m-rewards-simulator/pull/7) targeted the P0 branch after `#6` was squash-merged, so catalogs are **not on `main` yet**. Land via rebase of `cursor/web-managed-catalogs-1268` onto `main` before treating Postgres as the SKU source of truth. |
| **2** | **Review store-inclusion logic (Athena + simulator)** | **Next (with P3).** Spec: [`HANDOFF.md`](HANDOFF.md). Rick’s concern: stores without “enough” purchases may be dropped. There is **no** `HAVING` / min-quantity filter today. Investigate first; change the query only if the product rule is wrong. |
| **3** | **Per-brand ambient color** | **Next (with P2).** Spec: [`HANDOFF.md`](HANDOFF.md). One palette today (`--accent: #0f6a5a`). `data-brand` on `<body>` + CSS variables; keep layout/sectioning. Starter hex in the handoff if Rick has not supplied swatches. |
| **4** | **Create a new brand page from a template** | Depends on P1. After catalogs are on `main`, “new brand” is: display name + URL slug, upload the canonical Excel, at least one reward name + cutoff. Nav from the Postgres registry. Optional accent once P3 palettes exist. New SKUs only appear in simulations after an Athena refresh (or the 1st-of-month cron). |
