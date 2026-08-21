from datetime import date
import os
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from data import (
    backfill_windows,
    fetch_order_data,
    format_month_label,
    previous_month_window,
    replace_month_orders,
    replace_month_orders_frame,
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
