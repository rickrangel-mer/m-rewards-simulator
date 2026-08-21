from io import BytesIO
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


def _mocked_client():
    return (
        patch.object(
            webapp,
            "load_orders_or_error",
            return_value=(_sample_orders(), {"last_refreshed_month": "2026-07"}, None),
        ),
        patch.object(webapp, "load_brand_skus", return_value=_sample_skus()),
    )


def _html_between(html: str, start_id: str, end_id: str | None = None) -> str:
    start = html.index(f'id="{start_id}"')
    if end_id is None:
        return html[start:]
    end = html.index(f'id="{end_id}"')
    return html[start:end]


def test_health():
    client = TestClient(webapp.app)
    assert client.get("/health").json() == {"ok": True}


def test_home_redirects_to_coca_cola():
    client = TestClient(webapp.app)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/brands/coca-cola"


def test_brand_page_renders_with_mocked_data():
    orders_patch, skus_patch = _mocked_client()
    with orders_patch, skus_patch:
        client = TestClient(webapp.app)
        response = client.get("/brands/coca-cola")
    assert response.status_code == 200
    assert b"Coca-Cola" in response.content
    assert b"Order data through July 2026" in response.content
    assert b"SKU-A" in response.content
    assert b"Simulation Results" in response.content
    assert b"Stores earning each reward" in response.content
    assert b"stores" in response.content
    assert b"Using July 2026 ordering data" in response.content


def test_brand_page_sections_place_controls_with_outcomes():
    orders_patch, skus_patch = _mocked_client()
    with orders_patch, skus_patch:
        client = TestClient(webapp.app)
        response = client.get("/brands/coca-cola")
    html = response.text
    results = _html_between(html, "simulation-results", "sku-points")
    sku = _html_between(html, "sku-points", "reward-thresholds")
    rewards = _html_between(html, "reward-thresholds")

    assert 'id="month-form"' in results
    assert 'method="get"' in results
    assert "Simulation month" in results
    assert "Total Stores" in results
    assert "Stores earning each reward" in results
    assert "Points Distribution" in results
    assert "Store-level detail" in results
    assert "Search SKUs" not in results
    assert "Bulk point value" not in results
    assert "Import proposed points" not in results
    assert "Add Reward" not in results

    assert "Search SKUs" in sku
    assert "Bulk point value" in sku
    assert "Apply to selected" in sku
    assert "Import proposed points" in sku
    assert "Export SKU CSV" in sku
    assert "SKU-A" in sku
    assert "Simulation month" not in sku
    assert "Add Reward" not in sku

    assert "Add Reward" in rewards
    assert "Remove" in rewards
    assert "Search SKUs" not in rewards
    assert "Simulation month" not in rewards
    assert "Import proposed points" not in rewards


def test_all_brands_render_sectioned_pages():
    orders_patch, skus_patch = _mocked_client()
    with orders_patch, skus_patch:
        client = TestClient(webapp.app)
        for brand, label in (
            ("coca-cola", "Coca-Cola"),
            ("monster", "Monster"),
            ("ferrera", "Ferrera"),
        ):
            response = client.get(f"/brands/{brand}")
            assert response.status_code == 200, brand
            html = response.text
            assert label in html
            assert 'id="simulation-results"' in html
            assert 'id="sku-points"' in html
            assert 'id="reward-thresholds"' in html
            assert 'id="month-form"' in html
            assert "Update Simulation" in html


def test_month_query_keeps_results_in_sync():
    orders_patch, skus_patch = _mocked_client()
    with orders_patch, skus_patch:
        client = TestClient(webapp.app)
        response = client.get("/brands/coca-cola?month=2026-07")
    assert response.status_code == 200
    assert b"Using July 2026 ordering data" in response.content
    assert b'value="2026-07" selected' in response.content


def test_simulate_posts_results():
    orders_patch, skus_patch = _mocked_client()
    with orders_patch, skus_patch:
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
    assert b'id="sku-points"' in response.content
    assert b'id="reward-thresholds"' in response.content


def test_bulk_apply_preserves_month_and_flash():
    orders_patch, skus_patch = _mocked_client()
    with orders_patch, skus_patch:
        client = TestClient(webapp.app)
        response = client.post(
            "/brands/coca-cola/simulate",
            data={
                "month": "2026-07",
                "q": "",
                "bulk_value": "250",
                "action": "bulk_apply",
                "sku": "SKU-A",
                "proposed_points": "50",
                "selected_sku": "SKU-A",
                "reward_name": "Reward 1",
                "reward_points": "5000",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/brands/coca-cola?month=2026-07"
        follow = client.get(response.headers["location"])
    assert follow.status_code == 200
    assert b"Applied 250 points to 1 SKUs." in follow.content
    assert b'value="250"' in follow.content


def test_add_and_remove_reward_keep_month():
    orders_patch, skus_patch = _mocked_client()
    with orders_patch, skus_patch:
        client = TestClient(webapp.app)
        added = client.post(
            "/brands/coca-cola/simulate",
            data={
                "month": "2026-07",
                "action": "add_reward",
                "sku": "SKU-A",
                "proposed_points": "50",
                "reward_name": "Existing",
                "reward_points": "5000",
                "new_reward_name": "Bonus",
                "new_reward_points": "8000",
            },
            follow_redirects=False,
        )
        assert added.status_code == 303
        assert added.headers["location"] == "/brands/coca-cola?month=2026-07"
        page = client.get(added.headers["location"])
        assert b"Bonus" in page.content
        assert b'value="8000"' in page.content

        removed = client.post(
            "/brands/coca-cola/simulate",
            data={
                "month": "2026-07",
                "action": "remove_reward_1",
                "sku": "SKU-A",
                "proposed_points": "50",
                "reward_name": ["Existing", "Bonus"],
                "reward_points": ["5000", "8000"],
            },
            follow_redirects=False,
        )
        assert removed.status_code == 303
        page = client.get(removed.headers["location"])
    assert b"Bonus" not in page.content
    assert b"Existing" in page.content


def test_import_points_uses_hidden_month():
    orders_patch, skus_patch = _mocked_client()
    with orders_patch, skus_patch:
        client = TestClient(webapp.app)
        response = client.post(
            "/brands/coca-cola/import",
            data={"month": "2026-07"},
            files={"file": ("points.csv", BytesIO(b"sku,points\nSKU-A,400\n"), "text/csv")},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/brands/coca-cola?month=2026-07"
        page = client.get(response.headers["location"])
    assert b"Imported points for 1 SKUs." in page.content
    assert b'value="400"' in page.content
