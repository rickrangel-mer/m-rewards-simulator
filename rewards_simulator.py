import altair as alt
import openpyxl
import pandas as pd
import streamlit as st
from pathlib import Path

DATA_DIR = Path(__file__).parent
EXCEL_PATH = DATA_DIR / "M-rewards-cocacola.xlsx"
RAW_DATA_PATH = DATA_DIR / "rewards_raw_data.csv"
SKU_BEHAVIOR_PATH = DATA_DIR / "sku_monthly_behavior.csv"


@st.cache_data
def load_data():
    wb = openpyxl.load_workbook(EXCEL_PATH, read_only=True)
    ims = wb["ims"]
    sku_to_title = {}
    for row in ims.iter_rows(min_row=2, values_only=True):
        if row[3]:
            sku_to_title[row[3]] = row[4]

    ws = wb["WOS Ranked"]
    sku_records = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row[2]:
            continue
        sku_records.append({
            "sku": row[2],
            "product_title": sku_to_title.get(row[2], str(row[3])),
            "category": row[1],
            "rewards_match": row[11],
            "original_points": row[10],
        })
    wb.close()
    sku_map = pd.DataFrame(sku_records)

    behavior = pd.read_csv(SKU_BEHAVIOR_PATH)
    behavior = behavior[behavior["product_title"] != "Grand Total"]

    raw = pd.read_csv(RAW_DATA_PATH, parse_dates=["order_date"])
    may = raw[(raw["order_date"] >= "2026-05-01") & (raw["order_date"] < "2026-06-01")]
    may_orders = may.groupby(["store_id", "sku"])["total_quantity"].sum().reset_index()
    may_orders = may_orders.merge(
        sku_map[["sku", "product_title"]], on="sku", how="inner"
    )

    return sku_map, behavior, may_orders


def simulate(may_orders, points_lookup, reward_thresholds):
    may_with_points = may_orders.copy()
    may_with_points["points_per_unit"] = may_with_points["product_title"].map(points_lookup).fillna(0)
    may_with_points["points_earned"] = may_with_points["total_quantity"] * may_with_points["points_per_unit"]

    store_points = may_with_points.groupby("store_id").agg(
        total_points=("points_earned", "sum"),
        total_units=("total_quantity", "sum"),
        distinct_skus=("sku", "nunique"),
    ).reset_index()

    for reward_name, threshold in reward_thresholds.items():
        store_points[reward_name] = store_points["total_points"] >= threshold

    return store_points


def main():
    st.set_page_config(page_title="M-Rewards Simulator", layout="wide")
    st.title("M-Rewards Point Simulator")
    st.caption("Adjust point values per SKU and reward thresholds — simulation uses May 2026 ordering data")

    sku_map, behavior, may_orders = load_data()

    # --- SKU point editor ---
    st.subheader("SKU Point Editor")

    if "bulk_points" not in st.session_state:
        st.session_state.bulk_points = {}

    edit_df = behavior[["product_title", "Sum of store_penetration", "Count of rewards_match", "Sum of points", "Proposed Points"]].copy()
    edit_df.columns = ["Product Title", "Store Penetration", "Rewards Match", "Original Points", "Proposed Points"]
    edit_df["Proposed Points"] = edit_df["Proposed Points"].fillna(0).astype(int)
    edit_df["Original Points"] = edit_df["Original Points"].fillna(0).astype(int)
    edit_df["Rewards Match"] = edit_df["Rewards Match"].fillna(0).astype(int).replace({0: "", 1: "YES"})

    for title, pts in st.session_state.bulk_points.items():
        mask = edit_df["Product Title"] == title
        edit_df.loc[mask, "Proposed Points"] = pts

    bulk_col1, bulk_col2, bulk_col3 = st.columns([2, 1, 1])
    with bulk_col1:
        search = st.text_input("Search SKUs", placeholder="Type to filter...")
    with bulk_col2:
        bulk_value = st.number_input("Bulk point value", min_value=0, max_value=5000, value=100, step=50)
    with bulk_col3:
        st.markdown("<div style='height: 28px'></div>", unsafe_allow_html=True)
        apply_bulk = st.button("Apply to selected", use_container_width=True)

    edit_df["Select"] = False
    display_df = edit_df
    if search:
        mask = display_df["Product Title"].str.contains(search, case=False, na=False)
        display_df = display_df[mask]

    col_order = ["Select", "Product Title", "Store Penetration", "Rewards Match", "Original Points", "Proposed Points"]
    edited = st.data_editor(
        display_df[col_order],
        column_config={
            "Select": st.column_config.CheckboxColumn(default=False, width="small"),
            "Product Title": st.column_config.TextColumn(disabled=True, width="large"),
            "Store Penetration": st.column_config.NumberColumn(disabled=True),
            "Rewards Match": st.column_config.TextColumn(disabled=True),
            "Original Points": st.column_config.NumberColumn(disabled=True),
            "Proposed Points": st.column_config.NumberColumn(min_value=0, max_value=5000, step=50),
        },
        hide_index=True,
        width="stretch",
        key="sku_editor",
    )

    if apply_bulk:
        selected_titles = edited.loc[edited["Select"] == True, "Product Title"].tolist()
        if selected_titles:
            for title in selected_titles:
                st.session_state.bulk_points[title] = bulk_value
            st.rerun()

    points_lookup = dict(zip(edit_df["Product Title"], edit_df["Proposed Points"]))
    for _, row in edited.iterrows():
        points_lookup[row["Product Title"]] = row["Proposed Points"]
    for title, pts in st.session_state.bulk_points.items():
        points_lookup[title] = pts

    # --- Reward thresholds ---
    st.markdown("---")
    col_r1, col_r2, col_r3 = st.columns(3)
    with col_r1:
        r1 = st.number_input("8 Dollar Rebate (pts)", min_value=0, value=5000, step=500)
    with col_r2:
        r2 = st.number_input("Membership 9.99 (pts)", min_value=0, value=10000, step=500)
    with col_r3:
        r3 = st.number_input("Reward 3 TBD (pts)", min_value=0, value=15000, step=500)
    reward_thresholds = {
        "8 Dollar Rebate": r1,
        "Membership 9.99": r2,
        "Reward 3 (TBD)": r3,
    }

    # --- Simulation results ---
    st.markdown("---")
    store_points = simulate(may_orders, points_lookup, reward_thresholds)
    total_stores = len(store_points)

    st.subheader("Simulation Results")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Stores (May)", f"{total_stores:,}")
    m2.metric("Avg Points/Store", f"{store_points['total_points'].mean():,.0f}")
    m3.metric("Median Points/Store", f"{store_points['total_points'].median():,.0f}")
    m4.metric("Max Points/Store", f"{store_points['total_points'].max():,.0f}")

    r_cols = st.columns(3)
    for i, (name, threshold) in enumerate(reward_thresholds.items()):
        count = int(store_points[name].sum())
        pct = count / total_stores * 100 if total_stores > 0 else 0
        r_cols[i].metric(
            name,
            f"{count:,} stores ({pct:.1f}%)",
            f"{threshold:,} pts needed",
        )

    # --- Distribution chart ---
    st.markdown("---")
    st.subheader("Points Distribution")
    hist_data = store_points[["total_points"]].copy()
    hist_data["total_points"] = hist_data["total_points"].clip(
        upper=hist_data["total_points"].quantile(0.99)
    )

    bars = alt.Chart(hist_data).mark_bar(color="#4A90D9").encode(
        alt.X("total_points:Q", bin=alt.Bin(maxbins=50), title="Points Earned (May)"),
        alt.Y("count()", title="Number of Stores"),
    )
    rules = alt.Chart(pd.DataFrame([
        {"threshold": v, "label": k} for k, v in reward_thresholds.items()
    ])).mark_rule(strokeDash=[5, 5], strokeWidth=2).encode(
        x="threshold:Q",
        color=alt.Color("label:N", title="Reward Threshold"),
    )
    st.altair_chart(bars + rules, width="stretch")

    # --- Store detail ---
    with st.expander("Store-level detail"):
        display_cols = ["store_id", "total_points", "total_units", "distinct_skus"] + list(reward_thresholds.keys())
        st.dataframe(
            store_points[display_cols].sort_values("total_points", ascending=False),
            hide_index=True,
            width="stretch",
        )


if __name__ == "__main__":
    main()
