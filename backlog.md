# Product backlog

Priority **0** is highest (do next). **5** is lowest.

Brand page sectioning (month selector with results; SKU tools with the editor; reward controls with thresholds) shipped in [PR #3](https://github.com/rickrangel-mer/m-rewards-simulator/pull/3). FastAPI + Jinja2; no Streamlit.

**Direction:** Excel stays the *bulk edit format* (upload/download). Postgres owns brand SKUs, points, rewards, and the brand registry. The three git workbooks seed catalogs once. The public website must not stay open to anyone with the URL once suppliers are invited.

**Next:** P5. Spec + paste-ready prompt: [`HANDOFF.md`](HANDOFF.md). Login + roles so a supplier only sees the brand(s) Mercaso assigns.

| P | Item | Notes |
|---|---|---|
| **0** | **Persist proposed points, reward names, and thresholds** | **On `main`.** [PR #6](https://github.com/rickrangel-mer/m-rewards-simulator/pull/6). Working edits in `brand_proposed_points` / `brand_rewards`. |
| **1** | **Replace git Excel files with web-managed catalogs** | **On `main`.** Landed via [PR #9](https://github.com/rickrangel-mer/m-rewards-simulator/pull/9) (rebase of [PR #7](https://github.com/rickrangel-mer/m-rewards-simulator/pull/7)). Catalogs in `brand_skus`; Excel is upload/download only. |
| **2** | **Review store-inclusion logic (Athena + simulator)** | **On `main`.** [PR #9](https://github.com/rickrangel-mer/m-rewards-simulator/pull/9). Denominator is **A — current-month brand orderers**. No `HAVING`. `SUM(li.initial_quantity)` stays. |
| **3** | **Per-brand ambient color** | **On `main`.** [PR #9](https://github.com/rickrangel-mer/m-rewards-simulator/pull/9), palettes [PR #10](https://github.com/rickrangel-mer/m-rewards-simulator/pull/10), two-color split [PR #11](https://github.com/rickrangel-mer/m-rewards-simulator/pull/11). Coca-Cola red/white; Monster black + lime; Ferrera/Nerds pink/purple/blue. Flat gray page. |
| **4** | **Create a new brand page from a template** | **On `main`.** [PR #13](https://github.com/rickrangel-mer/m-rewards-simulator/pull/13), slug + SKU template [PR #14](https://github.com/rickrangel-mer/m-rewards-simulator/pull/14). Operator modal; nav from Postgres `brands`. |
| **5** | **Login and supplier brand access** | **Next.** Spec: [`HANDOFF.md`](HANDOFF.md). Email/password. Operator sees every brand. Supplier sees only assigned brand(s). Direct URLs to other brands 404. New brand + Pull order history stay operator-only. |
