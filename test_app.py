from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

import app as webapp


def _sample_orders():
    return pd.DataFrame({
        "store_id": ["S1", "S2"],
        "sku": ["SKU-A", "SKU-A"],
        "order_date": pd.to_datetime(["2026-07-10", "2026-07-12"]),
        "total_quantity": [5, 3],
    })


def _sample_skus():
    return pd.DataFrame({
        "sku": ["SKU-A"],
        "product_title": ["Test Product"],
        "current_points": [50],
    })


def test_health():
    client = TestClient(webapp.app)
    assert client.get("/health").json() == {"ok": True}


def test_home_redirects_to_coca_cola():
    client = TestClient(webapp.app)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/brands/coca-cola"


def test_brand_page_renders_with_mocked_data():
    with patch.object(webapp, "load_orders_or_error", return_value=(_sample_orders(), {"last_refreshed_month": "2026-07"}, None)), \
         patch.object(webapp, "load_brand_skus", return_value=_sample_skus()):
        client = TestClient(webapp.app)
        response = client.get("/brands/coca-cola")
    assert response.status_code == 200
    assert b"Coca-Cola" in response.content
    assert b"Order data through July 2026" in response.content
    assert b"SKU-A" in response.content


def test_simulate_posts_results():
    with patch.object(webapp, "load_orders_or_error", return_value=(_sample_orders(), {"last_refreshed_month": "2026-07"}, None)), \
         patch.object(webapp, "load_brand_skus", return_value=_sample_skus()):
        client = TestClient(webapp.app)
        response = client.post(
            "/brands/coca-cola/simulate",
            data={
                "month": "2026-07",
                "q": "",
                "bulk_value": "100",
                "action": "simulate",
                "sku": "SKU-A",
                "proposed_points": "100",
                "reward_name": "Reward 1",
                "reward_points": "100",
            },
        )
    assert response.status_code == 200
    assert b"Simulation Results" in response.content
    assert b"Total Stores" in response.content
