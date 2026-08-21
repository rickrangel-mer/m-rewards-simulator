# Product backlog

Priority **0** is highest (do next). **5** is lowest.

Brand page sectioning (month selector with results; SKU tools with the editor; reward controls with thresholds) shipped in [PR #3](https://github.com/rickrangel-mer/m-rewards-simulator/pull/3). FastAPI + Jinja2; no Streamlit. Web never talks to Athena.

**Direction:** Excel stays the *bulk edit format* (upload/download). It should not stay the *live config*. Postgres should own brand SKUs, points, and rewards; the three git workbooks (`M-rewards-cocacola.xlsx`, `M-rewards-monster.xlsx`, `M-rewards-ferrera.xlsx`) become a one-time seed, then leave runtime.

**Next:** P4. Spec + paste-ready prompt: [`HANDOFF.md`](HANDOFF.md). Operator **modal** to create a brand (display name, slug, participating SKUs Excel, at least one reward). Nav from a Postgres registry.

| P | Item | Notes |
|---|---|---|
| **0** | **Persist proposed points, reward names, and thresholds** | **On `main`.** [PR #6](https://github.com/rickrangel-mer/m-rewards-simulator/pull/6). Working edits in `brand_proposed_points` / `brand_rewards`. |
| **1** | **Replace git Excel files with web-managed catalogs** | **On `main`.** Landed via [PR #9](https://github.com/rickrangel-mer/m-rewards-simulator/pull/9) (rebase of [PR #7](https://github.com/rickrangel-mer/m-rewards-simulator/pull/7)). Catalogs in `brand_skus`; Excel is upload/download only. |
| **2** | **Review store-inclusion logic (Athena + simulator)** | **On `main`.** [PR #9](https://github.com/rickrangel-mer/m-rewards-simulator/pull/9). Denominator is **A — current-month brand orderers**. No `HAVING`. `SUM(li.initial_quantity)` stays. |
| **3** | **Per-brand ambient color** | **On `main`.** [PR #9](https://github.com/rickrangel-mer/m-rewards-simulator/pull/9), palettes [PR #10](https://github.com/rickrangel-mer/m-rewards-simulator/pull/10), two-color split [PR #11](https://github.com/rickrangel-mer/m-rewards-simulator/pull/11). Coca-Cola red/white; Monster black + lime; Ferrera/Nerds pink/purple/blue. Flat gray page. |
| **4** | **Create a new brand page from a template** | **Next.** Spec: [`HANDOFF.md`](HANDOFF.md). “Template” = operator **modal**, not a code stub. Requirements in the modal: display name, URL slug, participating SKUs Excel, at least one reward name + cutoff. Optional P3 palette. Nav from Postgres. New SKUs simulate after the next Athena refresh (or immediately if those SKUs already have order rows). |
