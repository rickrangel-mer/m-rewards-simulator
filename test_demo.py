import json
from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import demo


def _seed(tmp_path, slug="fizz"):
    store = demo.DemoStore(tmp_path)
    store.write_json(slug, "brand.json", {"label": "Fizz", "brand_name": "Fizz"})
    store.write_json(slug, "catalog.json", [{"sku": "F1", "product_title": "Fizz 12pk", "current_points": 50}])
    store.write_json(slug, "rewards.json", [["Hat", 3000, 3000]])
    orders = pd.DataFrame({
        "store_id": ["S1"] * 6,
        "sku": ["F1"] * 6,
        "order_date": [f"2026-{m:02d}-10" for m in range(1, 7)],
        "total_quantity": [100] * 6,
    })
    store.write_orders(slug, orders)
    return store


def test_demo_mode_serves_local_files_and_blocks_postgres(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://should-not-be-used/db")
    store = _seed(tmp_path)
    import app as webapp
    import data

    with demo.install(store), patch.object(webapp, "period_today", return_value=date(2026, 7, 15)):
        import os
        assert "DATABASE_URL" not in os.environ
        with pytest.raises(demo.DemoModeError):
            data.get_connection()
        client = TestClient(webapp.app)
        page = client.get("/brands/fizz")
        assert page.status_code == 200
        assert "Fizz 12pk" in page.text
        assert "Launch forecast" in page.text
        assert "Rewards claimed" in page.text

        # Edits land in demo_data, not Postgres.
        client.post("/brands/fizz/simulate", data={
            "month": "2026-06", "reward_name": "Cap", "reward_points": "4000", "reward_value": "12",
        })
    assert json.loads((tmp_path / "fizz" / "rewards.json").read_text()) == [["Cap", 4000, 1200]]
    # Patches are undone afterwards.
    assert data.get_connection is not demo._no_postgres


def test_demo_mode_without_data_explains_how_to_fetch(tmp_path):
    store = demo.DemoStore(tmp_path)
    raw, state, error = store.load_orders_or_error()
    assert raw is None
    assert "demo.py fetch" in error


def test_fetch_brand_writes_orders_catalog_and_rewards(tmp_path):
    orders = pd.DataFrame({
        "store_id": ["S1", "S2"], "sku": ["F1", "NOPTS"],
        "order_date": pd.to_datetime(["2026-05-03", "2026-05-04"]), "total_quantity": [4, 2],
    })
    import calibrate_launch
    with patch.object(demo, "fetch_orders", return_value=orders), \
         patch.object(demo, "fetch_titles", return_value={"F1": "Fizz 12pk"}), \
         patch.object(demo, "fetch_rewards", return_value=[("Hat", 3000, 3000)]), \
         patch.object(calibrate_launch, "find_partner", return_value="p1"), \
         patch.object(calibrate_launch, "infer_sku_points", return_value={"F1": 250.0}):
        folder = demo.fetch_brand("Fizz", demo_dir=tmp_path, conn=object(), today=date(2026, 11, 20))
    assert folder == tmp_path / "fizz"
    catalog = json.loads((folder / "catalog.json").read_text())
    assert catalog == [{"sku": "F1", "product_title": "Fizz 12pk", "current_points": 250}]
    saved = pd.read_csv(folder / "orders.csv", dtype={"sku": str})
    assert list(saved["sku"]) == ["F1"]  # SKUs that earn no points are left out
    assert json.loads((folder / "rewards.json").read_text()) == [["Hat", 3000, 3000]]
    assert json.loads((folder / "brand.json").read_text())["brand_name"] == "Fizz"


def test_month_window_is_whole_months():
    assert demo.month_window(12, date(2026, 11, 20)) == (date(2025, 11, 1), date(2026, 11, 1))
