from datetime import date
import os
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from data import (
    SCHEMA_SQL,
    backfill_windows,
    catalog_frame,
    diff_catalog,
    ensure_schema,
    fetch_order_data,
    format_month_label,
    init_schema,
    load_brand_rewards,
    load_catalog_skus,
    load_proposed_points,
    merge_proposed_points,
    overlay_catalog,
    overlay_proposed_points,
    parse_catalog_file,
    previous_month_window,
    replace_catalog_skus,
    replace_month_orders,
    replace_month_orders_frame,
    replace_proposed_points,
    save_brand_rewards,
    seed_brand_catalogs,
)
from refresh_orders import refresh_windows, run_refresh


def test_previous_month_window_september_pulls_august():
    start, end = previous_month_window(date(2026, 9, 1))
    assert start == date(2026, 8, 1)
    assert end == date(2026, 9, 1)


def test_previous_month_window_january_wraps_to_december():
    start, end = previous_month_window(date(2026, 1, 15))
    assert start == date(2025, 12, 1)
    assert end == date(2026, 1, 1)


def test_backfill_windows_are_six_complete_months_oldest_first():
    windows = backfill_windows(date(2026, 9, 1), num_months=6)
    assert windows[0] == (date(2026, 3, 1), date(2026, 4, 1))
    assert windows[-1] == (date(2026, 8, 1), date(2026, 9, 1))
    assert len(windows) == 6


def test_format_month_label():
    assert format_month_label("2026-08") == "August 2026"


def test_load_sku_mapping_reads_current_cocacola_workbook():
    from data import load_sku_mapping

    mapping = load_sku_mapping()
    assert not mapping.empty
    assert {"sku", "product_title", "category", "rewards_match", "points"} <= set(mapping.columns)
    assert mapping["sku"].notna().all()


def test_get_database_url_from_pg_vars():
    from data import get_database_url

    env = {
        "PGHOST": "postgres.railway.internal",
        "PGPORT": "5432",
        "PGUSER": "postgres",
        "PGPASSWORD": "secret",
        "PGDATABASE": "railway",
    }
    with patch.dict(os.environ, env, clear=True):
        url = get_database_url()
    assert "postgres.railway.internal" in url
    assert "postgresql://" in url


def test_get_database_url_rejects_unresolved_reference():
    from data import get_database_url

    with patch.dict(os.environ, {"DATABASE_URL": "${{Postgres.DATABASE_URL}}"}, clear=True):
        with pytest.raises(RuntimeError, match="Could not build"):
            get_database_url()


def test_safe_int_handles_nan_and_blank():
    from data import _safe_int
    import math

    assert _safe_int(None) == 0
    assert _safe_int(float("nan")) == 0
    assert _safe_int("") == 0
    assert _safe_int(50.9) == 50
    assert _safe_int("100") == 100
    assert not math.isnan(_safe_int(float("nan")))


def test_replace_month_orders_frame_replaces_only_target_month():
    existing = pd.DataFrame({
        "store_id": ["S1", "S1", "S2"],
        "sku": ["A", "A", "B"],
        "order_date": pd.to_datetime(["2026-07-10", "2026-08-10", "2026-08-20"]),
        "total_quantity": [1, 2, 3],
    })
    incoming = pd.DataFrame({
        "store_id": ["S3"],
        "sku": ["C"],
        "order_date": pd.to_datetime(["2026-08-15"]),
        "total_quantity": [9],
    })

    result = replace_month_orders_frame(
        existing, incoming, date(2026, 8, 1), date(2026, 9, 1)
    )

    months = result["order_date"].dt.to_period("M").astype(str)
    assert (months == "2026-07").sum() == 1
    assert (months == "2026-08").sum() == 1
    assert result.loc[result["sku"] == "C", "total_quantity"].iloc[0] == 9
    assert "A" not in set(result.loc[months == "2026-08", "sku"])


def test_replace_month_orders_deletes_then_inserts():
    incoming = pd.DataFrame({
        "store_id": ["S1"],
        "sku": ["SKU-1"],
        "order_date": pd.to_datetime(["2026-08-05"]),
        "total_quantity": [4],
    })
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with patch("psycopg2.extras.execute_values") as execute_values:
        inserted = replace_month_orders(conn, incoming, date(2026, 8, 1), date(2026, 9, 1))

    assert inserted == 1
    delete_sql, delete_params = cursor.execute.call_args[0]
    assert "DELETE FROM orders" in delete_sql
    assert delete_params == (date(2026, 8, 1), date(2026, 9, 1))
    execute_values.assert_called_once()
    rows = execute_values.call_args[0][2]
    assert rows == [("S1", "SKU-1", date(2026, 8, 5), 4.0)]
    conn.commit.assert_called()


def test_fetch_order_data_builds_exclusive_month_query(monkeypatch):
    captured = {}

    def fake_read_sql(query, conn):
        captured["query"] = query
        captured["conn"] = conn
        return pd.DataFrame({
            "store_id": ["S1"],
            "sku": ["SKU-1"],
            "order_date": ["2026-08-12"],
            "total_quantity": [7],
        })

    monkeypatch.setattr("data.pd.read_sql", fake_read_sql)
    df = fetch_order_data(
        ["SKU-1", "O'Brien"],
        date(2026, 8, 1),
        date(2026, 9, 1),
        connect_fn=lambda: "athena-conn",
    )

    assert captured["conn"] == "athena-conn"
    assert "TIMESTAMP '2026-08-01 00:00:00'" in captured["query"]
    assert "TIMESTAMP '2026-09-01 00:00:00'" in captured["query"]
    assert "'SKU-1'" in captured["query"]
    assert "'O''Brien'" in captured["query"]
    assert ">= TIMESTAMP" in captured["query"]
    assert "<  TIMESTAMP" in captured["query"] or "< TIMESTAMP" in captured["query"]
    assert list(df["sku"]) == ["SKU-1"]
    assert pd.api.types.is_datetime64_any_dtype(df["order_date"])


def test_refresh_windows_backfill_when_state_empty():
    windows = refresh_windows(date(2026, 9, 1), None)
    assert windows[0][0] == date(2026, 3, 1)
    assert windows[-1] == previous_month_window(date(2026, 9, 1))


def test_refresh_windows_previous_month_when_already_refreshed():
    windows = refresh_windows(
        date(2026, 9, 1),
        {"last_refreshed_month": "2026-07"},
    )
    assert windows == [previous_month_window(date(2026, 9, 1))]


def test_refresh_windows_force_backfill_env(monkeypatch):
    monkeypatch.setenv("REFRESH_BACKFILL", "1")
    windows = refresh_windows(
        date(2026, 9, 1),
        {"last_refreshed_month": "2026-08"},
    )
    assert len(windows) == 6
    assert windows[-1] == previous_month_window(date(2026, 9, 1))


def test_run_refresh_backfill_calls_mocked_athena_per_month(monkeypatch):
    monkeypatch.delenv("REFRESH_BACKFILL", raising=False)
    calls = []

    def fake_fetch(sku_list, start, end):
        calls.append((tuple(sku_list), start, end))
        return pd.DataFrame({
            "store_id": ["S1"],
            "sku": ["SKU-1"],
            "order_date": [pd.Timestamp(start)],
            "total_quantity": [1],
        })

    replaced = []

    def fake_replace(conn, orders, start, end):
        replaced.append((start, end, len(orders)))
        return len(orders)

    conn = MagicMock()
    with patch("refresh_orders.init_schema"), \
         patch("refresh_orders.get_refresh_state", return_value=None), \
         patch("refresh_orders.replace_month_orders", side_effect=fake_replace), \
         patch("refresh_orders.count_orders", return_value=6), \
         patch("refresh_orders.set_refresh_state") as set_state:
        result = run_refresh(
            today=date(2026, 9, 1),
            sku_list=["SKU-1"],
            fetch_fn=fake_fetch,
            conn=conn,
            num_months=6,
        )

    assert len(calls) == 6
    assert calls[-1][1:] == (date(2026, 8, 1), date(2026, 9, 1))
    assert replaced[-1][0] == date(2026, 8, 1)
    assert result["last_refreshed_month"] == "2026-08"
    assert result["windows"][-1] == (date(2026, 8, 1), date(2026, 9, 1))
    set_state.assert_called_once_with(conn, "2026-08", 6)


def test_run_refresh_subsequent_month_only_pulls_previous(monkeypatch):
    monkeypatch.delenv("REFRESH_BACKFILL", raising=False)
    calls = []

    def fake_fetch(sku_list, start, end):
        calls.append((start, end))
        return pd.DataFrame(columns=["store_id", "sku", "order_date", "total_quantity"])

    conn = MagicMock()
    with patch("refresh_orders.init_schema"), \
         patch("refresh_orders.get_refresh_state", return_value={"last_refreshed_month": "2026-07"}), \
         patch("refresh_orders.replace_month_orders", return_value=0), \
         patch("refresh_orders.count_orders", return_value=10), \
         patch("refresh_orders.set_refresh_state"):
        result = run_refresh(
            today=date(2026, 9, 1),
            sku_list=["SKU-1"],
            fetch_fn=fake_fetch,
            conn=conn,
        )

    assert calls == [(date(2026, 8, 1), date(2026, 9, 1))]
    assert result["last_refreshed_month"] == "2026-08"


def test_schema_sql_includes_proposed_and_rewards_tables():
    assert "brand_proposed_points" in SCHEMA_SQL
    assert "brand_rewards" in SCHEMA_SQL
    assert "brand_skus" in SCHEMA_SQL
    assert "PRIMARY KEY (brand, sku)" in SCHEMA_SQL
    assert "PRIMARY KEY (brand, sort)" in SCHEMA_SQL


def test_init_schema_executes_new_tables():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cursor

    init_schema(conn)

    sql_ran = "\n".join(call.args[0] for call in cursor.execute.call_args_list)
    assert "CREATE TABLE IF NOT EXISTS brand_proposed_points" in sql_ran
    assert "CREATE TABLE IF NOT EXISTS brand_rewards" in sql_ran
    assert "CREATE TABLE IF NOT EXISTS brand_skus" in sql_ran
    conn.commit.assert_called()


def test_ensure_schema_skips_without_database_url():
    with patch.dict(os.environ, {}, clear=True):
        with patch("data.get_connection") as get_connection:
            assert ensure_schema() is False
            get_connection.assert_not_called()


def test_ensure_schema_inits_when_url_present():
    conn = MagicMock()
    with patch("data.get_database_url", return_value="postgresql://example"), \
         patch("data.get_connection", return_value=conn), \
         patch("data.init_schema") as init, \
         patch("data.seed_brand_catalogs") as seed:
        assert ensure_schema() is True
        init.assert_called_once_with(conn)
        seed.assert_called_once_with(conn)
        conn.close.assert_called_once()


def test_overlay_proposed_points_keeps_omitted_and_drops_zero():
    existing = {"SKU-A": 100, "SKU-B": 200, "SKU-C": 300}
    patch = {"SKU-A": 150, "SKU-C": 0, "SKU-D": 90}
    assert overlay_proposed_points(existing, patch) == {
        "SKU-A": 150,
        "SKU-B": 200,
        "SKU-D": 90,
    }


def test_overlay_proposed_points_invalid_values_remove_override():
    existing = {"SKU-A": 100}
    assert overlay_proposed_points(existing, {"SKU-A": "nope"}) == {}


def test_load_proposed_points_skips_non_positive():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False
    cursor.fetchall.return_value = [("SKU-A", 100), ("SKU-B", 0)]
    conn = MagicMock()
    conn.cursor.return_value = cursor

    result = load_proposed_points("monster", conn=conn)

    assert result == {"SKU-A": 100}
    sql, params = cursor.execute.call_args[0]
    assert "FROM brand_proposed_points" in sql
    assert params == ("monster",)


def test_replace_proposed_points_deletes_then_inserts():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with patch("psycopg2.extras.execute_values") as execute_values:
        replace_proposed_points("coca-cola", {"SKU-A": 10, "SKU-B": 0}, conn=conn)

    delete_sql, delete_params = cursor.execute.call_args[0]
    assert "DELETE FROM brand_proposed_points" in delete_sql
    assert delete_params == ("coca-cola",)
    execute_values.assert_called_once()
    rows = execute_values.call_args[0][2]
    assert rows == [("coca-cola", "SKU-A", 10)]
    conn.commit.assert_called()


def test_merge_proposed_points_overlays_then_writes():
    conn = MagicMock()
    with patch("data.load_proposed_points", return_value={"SKU-A": 100, "SKU-B": 200}) as load, \
         patch("data.replace_proposed_points") as replace:
        merged = merge_proposed_points(
            "ferrera",
            {"SKU-A": 150, "SKU-C": 0},
            conn=conn,
        )

    assert merged == {"SKU-A": 150, "SKU-B": 200}
    load.assert_called_once_with("ferrera", conn=conn)
    replace.assert_called_once_with("ferrera", {"SKU-A": 150, "SKU-B": 200}, conn=conn)


def test_load_brand_rewards_orders_by_sort():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False
    cursor.fetchall.return_value = [("First", 5000), ("Second", 9000)]
    conn = MagicMock()
    conn.cursor.return_value = cursor

    result = load_brand_rewards("coca-cola", conn=conn)

    assert result == [("First", 5000), ("Second", 9000)]
    sql, params = cursor.execute.call_args[0]
    assert "ORDER BY sort" in sql
    assert params == ("coca-cola",)


def test_save_brand_rewards_replaces_rows():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with patch("psycopg2.extras.execute_values") as execute_values:
        save_brand_rewards("monster", [("Reward 1", 6500), ("Reward 2", 10000)], conn=conn)

    delete_sql, delete_params = cursor.execute.call_args[0]
    assert "DELETE FROM brand_rewards" in delete_sql
    assert delete_params == ("monster",)
    rows = execute_values.call_args[0][2]
    assert rows == [
        ("monster", 0, "Reward 1", 6500),
        ("monster", 1, "Reward 2", 10000),
    ]
    conn.commit.assert_called()


def test_parse_catalog_file_canonical_csv():
    csv = b"sku,product_title,current_points,size\nA,Cola,100,12oz\nB,Zero,50,\n"
    records, error = parse_catalog_file(csv, "catalog.csv")
    assert error is None
    by_sku = {r["sku"]: r for r in records}
    assert by_sku["A"]["product_title"] == "Cola"
    assert by_sku["A"]["current_points"] == 100
    assert by_sku["A"]["size"] == "12oz"
    assert "size" not in by_sku["B"]


def test_parse_catalog_file_requires_title():
    records, error = parse_catalog_file(b"sku,points\nA,100\n", "points.csv")
    assert records is None
    assert "product_title" in error


def test_parse_catalog_file_last_duplicate_wins():
    csv = b"sku,product_title,current_points\nA,First,1\nA,Second,2\n"
    records, error = parse_catalog_file(csv, "catalog.csv")
    assert error is None
    assert records == [{"sku": "A", "product_title": "Second", "current_points": 2}]


def test_overlay_catalog_keeps_omitted_and_appends_new():
    existing = [
        {"sku": "A", "product_title": "A", "current_points": 1},
        {"sku": "B", "product_title": "B", "current_points": 2},
    ]
    incoming = [
        {"sku": "A", "product_title": "A+", "current_points": 9},
        {"sku": "C", "product_title": "C", "current_points": 3},
    ]
    merged = overlay_catalog(existing, incoming)
    by_sku = {r["sku"]: r for r in merged}
    assert list(by_sku) == ["A", "B", "C"]
    assert by_sku["A"]["current_points"] == 9
    assert by_sku["B"]["current_points"] == 2


def test_diff_catalog_classifies_add_remove_update():
    existing = [
        {"sku": "A", "product_title": "A", "current_points": 1},
        {"sku": "B", "product_title": "B", "current_points": 2},
    ]
    incoming = [
        {"sku": "A", "product_title": "A+", "current_points": 1},
        {"sku": "C", "product_title": "C", "current_points": 3},
    ]
    diff = diff_catalog(existing, incoming)
    assert [r["sku"] for r in diff["added"]] == ["C"]
    assert [r["sku"] for r in diff["removed"]] == ["B"]
    assert diff["updated"][0]["sku"] == "A"
    assert diff["incoming_count"] == 2
    assert diff["existing_count"] == 2


def test_catalog_frame_round_trip():
    records = [
        {"sku": "A", "product_title": "Cola", "current_points": 10, "brand": "CC"},
    ]
    df = catalog_frame(records)
    assert list(df["sku"]) == ["A"]
    assert int(df["current_points"].iloc[0]) == 10
    assert df["brand"].iloc[0] == "CC"


def test_seed_brand_catalogs_skips_when_populated():
    with patch("data.count_catalog_skus", return_value=4), \
         patch("data.replace_catalog_skus") as replace:
        seeded = seed_brand_catalogs(conn="fake")
    assert seeded == {}
    replace.assert_not_called()


def test_seed_brand_catalogs_inserts_when_empty():
    sample = pd.DataFrame({
        "sku": ["S1"],
        "product_title": ["P1"],
        "current_points": [5],
    })
    with patch("data.count_catalog_skus", return_value=0), \
         patch("data.excel_skus_for_brand", return_value=sample), \
         patch("data.replace_catalog_skus", return_value=1) as replace:
        seeded = seed_brand_catalogs(conn="fake")
    assert seeded == {"coca-cola": 1, "monster": 1, "ferrera": 1}
    assert replace.call_count == 3


def test_load_catalog_skus_maps_product_brand():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False
    cursor.fetchall.return_value = [("SKU-1", "Candy", 18, None, "Ferrero", "Treats")]
    conn = MagicMock()
    conn.cursor.return_value = cursor

    result = load_catalog_skus("ferrera", conn=conn)

    assert result == [{
        "sku": "SKU-1",
        "product_title": "Candy",
        "current_points": 18,
        "brand": "Ferrero",
        "category": "Treats",
    }]


def test_replace_catalog_skus_deletes_then_inserts_and_prunes_proposed():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = False
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with patch("psycopg2.extras.execute_values") as execute_values:
        count = replace_catalog_skus(
            "coca-cola",
            [{"sku": "SKU-A", "product_title": "A", "current_points": 10}],
            conn=conn,
        )

    assert count == 1
    executed = [call.args[0] for call in cursor.execute.call_args_list]
    assert any("DELETE FROM brand_skus" in sql for sql in executed)
    assert any("DELETE FROM brand_proposed_points" in sql for sql in executed)
    rows = execute_values.call_args[0][2]
    assert rows[0][:4] == ("coca-cola", "SKU-A", "A", 10)
    conn.commit.assert_called()
