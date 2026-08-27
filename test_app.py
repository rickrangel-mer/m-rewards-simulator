from datetime import date
from io import BytesIO
from unittest.mock import patch
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app as webapp
from data import DuplicateBrandError, overlay_catalog, overlay_proposed_points
from simulator import BRAND_DEFAULTS


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


def _two_skus():
    return pd.DataFrame({
        "sku": ["SKU-A", "SKU-B"],
        "product_title": ["Product A", "Product B"],
        "current_points": [50, 75],
    })


class FakeStore:
    """In-memory stand-in for Railway Postgres proposed-points / rewards / catalog / brands tables."""

    def __init__(self):
        self.proposed: dict[str, dict[str, int]] = {}
        self.rewards: dict[str, list[tuple[str, int]]] = {}
        self.catalogs: dict[str, list[dict]] = {}
        self.brands: dict[str, dict] = {
            "coca-cola": {"label": "Coca-Cola", "theme": "coca-cola", "sort": 0},
            "monster": {"label": "Monster", "theme": "monster", "sort": 1},
            "ferrera": {"label": "Ferrera", "theme": "ferrera", "sort": 2},
        }

    def load_proposed_points(self, brand: str, conn=None) -> dict[str, int]:
        return dict(self.proposed.get(brand, {}))

    def merge_proposed_points(self, brand: str, patch: dict[str, int], conn=None) -> dict[str, int]:
        merged = overlay_proposed_points(self.proposed.get(brand, {}), patch)
        self.proposed[brand] = merged
        return dict(merged)

    def load_brand_rewards(self, brand: str, conn=None) -> list[tuple[str, int]]:
        stored = self.rewards.get(brand)
        if not stored:
            return []
        return list(stored)

    def save_brand_rewards(self, brand: str, rewards: list[tuple[str, int]], conn=None) -> None:
        self.rewards[brand] = [(str(n), int(p)) for n, p in rewards]

    def load_catalog_skus(self, brand: str, conn=None) -> list[dict]:
        return [dict(r) for r in self.catalogs.get(brand, [])]

    def replace_catalog_skus(self, brand: str, records: list[dict], conn=None) -> int:
        normalized = overlay_catalog([], records)
        self.catalogs[brand] = normalized
        keep = {r["sku"] for r in normalized}
        proposed = self.proposed.get(brand, {})
        self.proposed[brand] = {sku: pts for sku, pts in proposed.items() if sku in keep}
        return len(normalized)

    def merge_catalog_skus(self, brand: str, records: list[dict], conn=None) -> list[dict]:
        merged = overlay_catalog(self.catalogs.get(brand, []), records)
        self.replace_catalog_skus(brand, merged)
        return [dict(r) for r in merged]

    def load_brands(self, conn=None) -> list[dict]:
        rows = [
            {
                "slug": slug,
                "label": meta["label"],
                "theme": meta.get("theme") or "default",
                "sort": int(meta.get("sort", 0)),
            }
            for slug, meta in self.brands.items()
        ]
        return sorted(rows, key=lambda row: (row["sort"], row["slug"]))

    def get_brand(self, slug: str, conn=None) -> dict | None:
        meta = self.brands.get(slug)
        if not meta:
            return None
        return {
            "slug": slug,
            "label": meta["label"],
            "theme": meta.get("theme") or "default",
            "sort": int(meta.get("sort", 0)),
        }

    def create_brand(self, slug: str, label: str, theme: str, records: list[dict], rewards: list[tuple[str, int]], conn=None) -> dict:
        if slug in self.brands:
            raise DuplicateBrandError(slug)
        sort = max((int(meta.get("sort", 0)) for meta in self.brands.values()), default=-1) + 1
        self.brands[slug] = {"label": label, "theme": theme, "sort": sort}
        self.replace_catalog_skus(slug, records)
        self.save_brand_rewards(slug, rewards)
        return {"slug": slug, "label": label, "theme": theme, "sort": sort}


@pytest.fixture
def persist_store():
    return FakeStore()


@pytest.fixture(autouse=True)
def _patch_persist(persist_store):
    with patch.object(webapp, "load_proposed_points", persist_store.load_proposed_points), \
         patch.object(webapp, "merge_proposed_points", persist_store.merge_proposed_points), \
         patch.object(webapp, "load_brand_rewards", persist_store.load_brand_rewards), \
         patch.object(webapp, "save_brand_rewards", persist_store.save_brand_rewards), \
         patch.object(webapp, "load_catalog_skus", persist_store.load_catalog_skus), \
         patch.object(webapp, "replace_catalog_skus", persist_store.replace_catalog_skus), \
         patch.object(webapp, "merge_catalog_skus", persist_store.merge_catalog_skus), \
         patch.object(webapp, "load_brands", persist_store.load_brands), \
         patch.object(webapp, "get_brand", persist_store.get_brand), \
         patch.object(webapp, "create_brand", persist_store.create_brand):
        yield persist_store


def _mocked_client(skus=None):
    return (
        patch.object(
            webapp,
            "load_orders_or_error",
            return_value=(_sample_orders(), {"last_refreshed_month": "2026-07"}, None),
        ),
        patch.object(webapp, "load_brand_skus", return_value=skus if skus is not None else _sample_skus()),
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
    assert 'id="brand-refresh-form"' in results
    assert results.index('id="brand-refresh-form"') < results.index('id="month-form"')
    assert "Pull order history" in results
    assert 'method="get"' in results
    assert "Simulation month" in results
    assert "Total Stores" in results
    assert "stores that ordered this brand this month" in results
    assert "Chart clips the top 1%" in results
    assert "Stores earning each reward" in results
    assert "Points Distribution" in results
    assert "Store-level detail" in results
    assert 'class="data-table"' in results
    assert "/static/tables.js" in html
    assert "Search SKUs" not in results
    assert "Bulk point value" not in results
    assert "Import proposed points" not in results
    assert "Add Reward" not in results
    assert "Upload catalog" not in results
    assert "Download catalog Excel" not in results

    assert "Search SKUs" in sku
    assert "Bulk point value" in sku
    assert "Apply to selected" in sku
    assert "Import proposed points" in sku
    assert "Export SKU CSV" in sku
    assert "Upload catalog" in sku
    assert "Preview catalog" in sku
    assert "Download catalog Excel" in sku
    assert 'class="data-table"' in sku
    assert "SKU-A" in sku
    assert "Saved for everyone" in sku
    assert "Simulation month" not in sku
    assert "Add Reward" not in sku
    assert "Pull order history" not in sku
    assert 'name="proposed_points"' in sku
    assert 'max="5000"' not in sku
    assert 'name="bulk_value"' in sku

    assert "Add Reward" in rewards
    assert "Saved for everyone" in rewards
    assert "Remove" in rewards
    assert "Search SKUs" not in rewards
    assert "Simulation month" not in rewards
    assert "Import proposed points" not in rewards
    assert "Upload catalog" not in rewards
    assert "Pull order history" not in rewards


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
            assert 'id="brand-refresh-form"' in html
            assert "Pull order history" in html
            assert 'class="data-table"' in html
            assert "/static/tables.js" in html
            assert "Update Simulation" in html
            assert "Download catalog Excel" in html
            assert 'id="catalog-upload"' in html
            assert f'data-brand="{brand}"' in html
            assert f'data-theme="{brand}"' in html
            assert "stores that ordered this brand this month" in html
            assert "New brand" in html
            assert 'id="new-brand-dialog"' in html


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


def test_import_points_without_month_does_not_return_json_422():
    orders_patch, skus_patch = _mocked_client()
    with orders_patch, skus_patch:
        client = TestClient(webapp.app)
        response = client.post(
            "/brands/coca-cola/import",
            files={"file": ("points.csv", BytesIO(b"sku,points\nSKU-A,400\n"), "text/csv")},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "application/json" not in response.headers.get("content-type", "")
        page = client.get(response.headers["location"])
    assert b"Imported points for 1 SKUs." in page.content
    assert b'value="400"' in page.content


def test_import_points_accepts_sales_workbook_columns(persist_store):
    csv = (
        b"sku,Product Title ,Package Size,Brand,Sales (4 WOS),points\n"
        b"SKU-A,Red Bull,24,Red Bull,1389,50\n"
    )
    orders_patch, skus_patch = _mocked_client()
    with orders_patch, skus_patch:
        client = TestClient(webapp.app)
        response = client.post(
            "/brands/coca-cola/import",
            files={"file": ("Redbull Monthly Sales.csv", BytesIO(csv), "text/csv")},
            follow_redirects=False,
        )
        assert response.status_code == 303
        page = client.get(response.headers["location"])
    assert persist_store.proposed["coca-cola"]["SKU-A"] == 50
    assert b"Imported points for 1 SKUs." in page.content


def test_import_missing_file_redirects_instead_of_json_422():
    orders_patch, skus_patch = _mocked_client()
    with orders_patch, skus_patch:
        client = TestClient(webapp.app)
        response = client.post(
            "/brands/coca-cola/import",
            data={"month": "2026-07"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "application/json" not in response.headers.get("content-type", "")
        page = client.get(response.headers["location"])
    assert b"Choose a CSV or Excel file" in page.content


def test_simulate_round_trips_without_cookies(persist_store):
    orders_patch, skus_patch = _mocked_client()
    with orders_patch, skus_patch:
        writer = TestClient(webapp.app)
        posted = writer.post(
            "/brands/coca-cola/simulate",
            data={
                "month": "2026-07",
                "q": "",
                "bulk_value": "100",
                "action": "simulate",
                "sku": "SKU-A",
                "proposed_points": "333",
                "reward_name": "Team Cooler",
                "reward_points": "1234",
            },
        )
        assert posted.status_code == 200

        reader = TestClient(webapp.app)
        page = reader.get("/brands/coca-cola")

    assert persist_store.proposed["coca-cola"]["SKU-A"] == 333
    assert persist_store.rewards["coca-cola"] == [("Team Cooler", 1234)]
    assert b'value="333"' in page.content
    assert b"Team Cooler" in page.content
    assert b'value="1234"' in page.content


def test_proposed_points_above_former_5000_cap_round_trip(persist_store):
    orders_patch, skus_patch = _mocked_client()
    with orders_patch, skus_patch:
        client = TestClient(webapp.app)
        posted = client.post(
            "/brands/coca-cola/simulate",
            data={
                "month": "2026-07",
                "action": "simulate",
                "sku": "SKU-A",
                "proposed_points": "8000",
                "reward_name": "Reward 1",
                "reward_points": "5000",
            },
        )
        assert posted.status_code == 200
        html = posted.text
        sku = _html_between(html, "sku-points", "reward-thresholds")
        assert 'max="5000"' not in sku
        page = client.get("/brands/coca-cola")
    assert persist_store.proposed["coca-cola"]["SKU-A"] == 8000
    assert b'value="8000"' in page.content


def test_search_filtered_post_does_not_wipe_other_skus(persist_store):
    persist_store.proposed["coca-cola"] = {"SKU-A": 100, "SKU-B": 200}
    orders_patch, skus_patch = _mocked_client(skus=_two_skus())
    with orders_patch, skus_patch:
        client = TestClient(webapp.app)
        response = client.post(
            "/brands/coca-cola/simulate",
            data={
                "month": "2026-07",
                "q": "SKU-A",
                "bulk_value": "100",
                "action": "simulate",
                "sku": "SKU-A",
                "proposed_points": "150",
                "reward_name": "Reward 1",
                "reward_points": "5000",
            },
        )
        assert response.status_code == 200
        full = client.get("/brands/coca-cola")

    assert persist_store.proposed["coca-cola"] == {"SKU-A": 150, "SKU-B": 200}
    assert b'value="150"' in full.content
    assert b'value="200"' in full.content


def test_zero_proposed_points_removes_override(persist_store):
    persist_store.proposed["coca-cola"] = {"SKU-A": 100, "SKU-B": 200}
    orders_patch, skus_patch = _mocked_client(skus=_two_skus())
    with orders_patch, skus_patch:
        client = TestClient(webapp.app)
        client.post(
            "/brands/coca-cola/simulate",
            data={
                "month": "2026-07",
                "action": "simulate",
                "sku": "SKU-A",
                "proposed_points": "0",
                "reward_name": "Reward 1",
                "reward_points": "5000",
            },
        )
    assert persist_store.proposed["coca-cola"] == {"SKU-B": 200}


def test_import_merges_onto_existing_proposed_points(persist_store):
    persist_store.proposed["coca-cola"] = {"SKU-B": 200}
    orders_patch, skus_patch = _mocked_client(skus=_two_skus())
    with orders_patch, skus_patch:
        client = TestClient(webapp.app)
        response = client.post(
            "/brands/coca-cola/import",
            data={"month": "2026-07"},
            files={"file": ("points.csv", BytesIO(b"sku,points\nSKU-A,400\n"), "text/csv")},
            follow_redirects=False,
        )
        page = client.get(response.headers["location"])
    assert persist_store.proposed["coca-cola"] == {"SKU-A": 400, "SKU-B": 200}
    assert b'value="400"' in page.content
    assert b'value="200"' in page.content


def test_add_and_remove_reward_round_trips_without_cookies(persist_store):
    orders_patch, skus_patch = _mocked_client()
    with orders_patch, skus_patch:
        writer = TestClient(webapp.app)
        added = writer.post(
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

        reader = TestClient(webapp.app)
        page = reader.get("/brands/coca-cola")
        assert b"Bonus" in page.content
        assert persist_store.rewards["coca-cola"] == [("Existing", 5000), ("Bonus", 8000)]

        writer.post(
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
        page = reader.get("/brands/coca-cola")
    assert b"Bonus" not in page.content
    assert b"Existing" in page.content
    assert persist_store.rewards["coca-cola"] == [("Existing", 5000)]


def test_persist_round_trip_all_brands(persist_store):
    orders_patch, skus_patch = _mocked_client()
    with orders_patch, skus_patch:
        writer = TestClient(webapp.app)
        for brand in BRAND_DEFAULTS:
            posted = writer.post(
                f"/brands/{brand}/simulate",
                data={
                    "month": "2026-07",
                    "action": "simulate",
                    "sku": "SKU-A",
                    "proposed_points": "222",
                    "reward_name": f"{brand} shared reward",
                    "reward_points": "7777",
                },
            )
            assert posted.status_code == 200, brand

        reader = TestClient(webapp.app)
        for brand, meta in BRAND_DEFAULTS.items():
            page = reader.get(f"/brands/{brand}")
            assert page.status_code == 200, brand
            html = page.text
            assert meta["label"] in html
            assert 'id="simulation-results"' in html
            assert 'id="sku-points"' in html
            assert 'id="reward-thresholds"' in html
            assert f"{brand} shared reward" in html
            assert 'value="222"' in html
            assert 'value="7777"' in html
            assert persist_store.proposed[brand]["SKU-A"] == 222
            assert persist_store.rewards[brand] == [(f"{brand} shared reward", 7777)]


def test_empty_store_serves_brand_defaults(persist_store):
    orders_patch, skus_patch = _mocked_client()
    with orders_patch, skus_patch:
        client = TestClient(webapp.app)
        page = client.get("/brands/coca-cola")
    assert persist_store.rewards == {}
    assert persist_store.proposed == {}
    assert b"8 Dollar Rebate" in page.content
    assert b'value="5000"' in page.content


def test_web_startup_calls_ensure_schema():
    with patch.object(webapp, "ensure_schema", return_value=False) as ensure:
        with TestClient(webapp.app) as client:
            assert client.get("/health").json() == {"ok": True}
        ensure.assert_called()


def test_download_catalog_excel(persist_store):
    persist_store.catalogs["coca-cola"] = [
        {"sku": "SKU-A", "product_title": "Test Product", "current_points": 50, "size": "12oz"},
    ]
    client = TestClient(webapp.app)
    response = client.get("/brands/coca-cola/catalog.xlsx")
    assert response.status_code == 200
    assert "spreadsheetml" in response.headers["content-type"]
    df = pd.read_excel(BytesIO(response.content))
    assert list(df["sku"]) == ["SKU-A"]
    assert list(df["product_title"]) == ["Test Product"]
    assert int(df["current_points"].iloc[0]) == 50
    assert "size" in df.columns


def test_download_catalog_all_brands(persist_store):
    client = TestClient(webapp.app)
    for brand in BRAND_DEFAULTS:
        persist_store.catalogs[brand] = [
            {"sku": "SKU-A", "product_title": f"{brand} product", "current_points": 10},
        ]
        response = client.get(f"/brands/{brand}/catalog.xlsx")
        assert response.status_code == 200, brand
        df = pd.read_excel(BytesIO(response.content))
        assert list(df["sku"]) == ["SKU-A"]


def test_catalog_preview_shows_add_update_remove(persist_store):
    persist_store.catalogs["coca-cola"] = [
        {"sku": "SKU-A", "product_title": "Old Name", "current_points": 50},
        {"sku": "SKU-B", "product_title": "Keep Me", "current_points": 75},
    ]
    csv = b"sku,product_title,current_points\nSKU-A,New Name,80\nSKU-C,Added,10\n"
    client = TestClient(webapp.app)
    response = client.post(
        "/brands/coca-cola/catalog",
        data={"month": "2026-07"},
        files={"file": ("catalog.csv", BytesIO(csv), "text/csv")},
    )
    assert response.status_code == 200
    html = response.text
    assert "Preview of" in html
    assert "SKU-C" in html
    assert "Added" in html
    assert "Keep Me" in html
    assert "Old Name" in html
    assert "New Name" in html
    assert "Merge into catalog" in html
    assert "Replace catalog" in html
    assert 'id="catalog-confirm"' in html
    assert 'data-brand="coca-cola"' in html
    assert 'class="data-table"' in html
    assert "/static/tables.js" in html


def test_catalog_merge_keeps_omitted_skus(persist_store):
    persist_store.catalogs["coca-cola"] = [
        {"sku": "SKU-A", "product_title": "A", "current_points": 50},
        {"sku": "SKU-B", "product_title": "B", "current_points": 75},
    ]
    persist_store.proposed["coca-cola"] = {"SKU-A": 9, "SKU-B": 8}
    incoming = [
        {"sku": "SKU-A", "product_title": "A+", "current_points": 60},
        {"sku": "SKU-C", "product_title": "C", "current_points": 5},
    ]
    client = TestClient(webapp.app)
    response = client.post(
        "/brands/coca-cola/catalog/confirm",
        data={
            "month": "2026-07",
            "action": "merge",
            "payload": json.dumps(incoming),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    skus = {r["sku"]: r for r in persist_store.catalogs["coca-cola"]}
    assert set(skus) == {"SKU-A", "SKU-B", "SKU-C"}
    assert skus["SKU-A"]["product_title"] == "A+"
    assert skus["SKU-A"]["current_points"] == 60
    assert skus["SKU-B"]["current_points"] == 75
    assert persist_store.proposed["coca-cola"] == {"SKU-A": 9, "SKU-B": 8}


def test_catalog_replace_removes_omitted_skus(persist_store):
    persist_store.catalogs["coca-cola"] = [
        {"sku": "SKU-A", "product_title": "A", "current_points": 50},
        {"sku": "SKU-B", "product_title": "B", "current_points": 75},
    ]
    persist_store.proposed["coca-cola"] = {"SKU-A": 9, "SKU-B": 8}
    incoming = [
        {"sku": "SKU-A", "product_title": "A+", "current_points": 60},
        {"sku": "SKU-C", "product_title": "C", "current_points": 5},
    ]
    client = TestClient(webapp.app)
    response = client.post(
        "/brands/coca-cola/catalog/confirm",
        data={
            "month": "2026-07",
            "action": "replace",
            "payload": json.dumps(incoming),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    skus = {r["sku"]: r for r in persist_store.catalogs["coca-cola"]}
    assert set(skus) == {"SKU-A", "SKU-C"}
    assert persist_store.proposed["coca-cola"] == {"SKU-A": 9}


def test_catalog_replace_round_trip_without_cookies(persist_store):
    persist_store.catalogs["coca-cola"] = [
        {"sku": "SKU-A", "product_title": "A", "current_points": 50},
        {"sku": "SKU-B", "product_title": "B", "current_points": 75},
    ]
    incoming = [
        {"sku": "SKU-A", "product_title": "Zed", "current_points": 12},
        {"sku": "SKU-Z", "product_title": "New SKU", "current_points": 7},
    ]
    writer = TestClient(webapp.app)
    posted = writer.post(
        "/brands/coca-cola/catalog/confirm",
        data={"month": "2026-07", "action": "replace", "payload": json.dumps(incoming)},
        follow_redirects=False,
    )
    assert posted.status_code == 303

    orders_patch, _unused = _mocked_client()
    with orders_patch, patch.object(
        webapp,
        "load_brand_skus",
        side_effect=lambda brand: webapp.catalog_frame(persist_store.catalogs.get(brand, [])),
    ):
        page = TestClient(webapp.app).get("/brands/coca-cola")
    assert page.status_code == 200
    assert b"SKU-Z" in page.content
    assert b"New SKU" in page.content
    assert b"Zed" in page.content
    assert b"SKU-B" not in page.content


def test_catalog_upload_rejects_points_only_file(persist_store):
    client = TestClient(webapp.app)
    response = client.post(
        "/brands/coca-cola/catalog",
        data={"month": "2026-07"},
        files={"file": ("points.csv", BytesIO(b"sku,points\nSKU-A,400\n"), "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    orders_patch, skus_patch = _mocked_client()
    with orders_patch, skus_patch:
        follow = client.get(response.headers["location"])
    assert b"product_title" in follow.content
    assert persist_store.catalogs.get("coca-cola") in (None, [])


def test_brand_theme_css_uses_variables_and_palettes():
    client = TestClient(webapp.app)
    css = client.get("/static/styles.css").text
    assert 'body[data-theme="coca-cola"]' in css
    assert "#f40009" in css
    assert 'body[data-theme="monster"]' in css
    assert "#8dc63f" in css
    assert "#111111" in css
    assert 'body[data-theme="ferrera"]' in css
    assert "#e4007c" in css
    assert "#5c2d91" in css
    assert "#1e3fa8" in css
    assert "--cta" in css
    assert "--bar" in css
    assert "#3d8f7f" not in css
    assert "rgba(15, 106, 90" not in css
    assert "circle at top left" not in css
    assert ".bar-fill" in css
    assert "background: var(--bar)" in css
    assert "background: var(--bg)" in css
    assert "th.sortable" in css


def test_table_sort_script_is_served():
    client = TestClient(webapp.app)
    js = client.get("/static/tables.js").text
    assert "sortTable" in js
    assert "aria-sort" in js


def _live_catalog(persist_store, orders=None):
    orders = _sample_orders() if orders is None else orders
    return (
        patch.object(
            webapp,
            "load_orders_or_error",
            return_value=(orders, {"last_refreshed_month": "2026-07"}, None),
        ),
        patch.object(
            webapp,
            "load_brand_skus",
            side_effect=lambda brand: webapp.catalog_frame(persist_store.catalogs.get(brand, [])),
        ),
    )


def _create_brand_form(**overrides):
    data = {
        "label": "Pepsi",
        "theme": "default",
        "reward_name": "Cooler",
        "reward_points": "5000",
        "return_to": "/brands/coca-cola",
    }
    data.update(overrides)
    return data


def _create_catalog_file(body=b"sku,product_title,current_points\nSKU-A,Pepsi Cola,40\n", name="catalog.csv"):
    return {"file": (name, BytesIO(body), "text/csv")}


def test_new_brand_modal_lists_requirements():
    orders_patch, skus_patch = _mocked_client()
    with orders_patch, skus_patch:
        client = TestClient(webapp.app)
        html = client.get("/brands/coca-cola").text
    assert 'id="new-brand-dialog"' in html
    assert "New brand" in html
    assert "Display name" in html
    assert "URL slug" not in html
    assert "Participating SKUs Excel/CSV" in html
    assert "sku, product_title, current_points" in html
    assert "Download template" in html
    assert 'href="/catalog-template.xlsx"' in html
    assert "At least one reward" in html
    assert "Pull order history" in html
    assert "next monthly Athena refresh" in html
    assert 'action="/brands/create"' in html
    assert "Palette (optional)" in html


def test_create_brand_persists_and_renders_sectioned_page(persist_store):
    orders_patch, skus_patch = _live_catalog(persist_store)
    with orders_patch, skus_patch:
        client = TestClient(webapp.app)
        response = client.post(
            "/brands/create",
            data=_create_brand_form(theme="coca-cola"),
            files=_create_catalog_file(),
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/brands/pepsi"
        page = client.get("/brands/pepsi")

    assert persist_store.brands["pepsi"]["label"] == "Pepsi"
    assert persist_store.brands["pepsi"]["theme"] == "coca-cola"
    assert persist_store.catalogs["pepsi"][0]["sku"] == "SKU-A"
    assert persist_store.rewards["pepsi"] == [("Cooler", 5000)]
    assert page.status_code == 200
    html = page.text
    assert "Pepsi" in html
    assert ">Pepsi</a>" in html
    assert 'id="simulation-results"' in html
    assert 'id="sku-points"' in html
    assert 'id="reward-thresholds"' in html
    assert "Pepsi Cola" in html
    assert "Cooler" in html
    assert 'data-brand="pepsi"' in html
    assert 'data-theme="coca-cola"' in html
    assert "Total Stores" in html


def test_create_brand_with_new_skus_renders_before_refresh(persist_store):
    csv = b"sku,product_title,current_points\nSKU-Z,Net New,10\n"
    orders_patch, skus_patch = _live_catalog(persist_store)
    with orders_patch, skus_patch:
        client = TestClient(webapp.app)
        response = client.post(
            "/brands/create",
            data=_create_brand_form(label="Zevia", theme="monster"),
            files=_create_catalog_file(csv),
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/brands/zevia"
        page = client.get("/brands/zevia")

    assert page.status_code == 200
    html = page.text
    assert "Zevia" in html
    assert "SKU-Z" in html
    assert "Net New" in html
    assert 'id="simulation-results"' in html
    assert 'id="sku-points"' in html
    assert 'id="reward-thresholds"' in html
    assert "No order months yet" in html
    assert 'data-brand="zevia"' in html
    assert 'data-theme="monster"' in html


def test_create_brand_missing_file_does_not_write(persist_store):
    before = dict(persist_store.brands)
    client = TestClient(webapp.app)
    response = client.post(
        "/brands/create",
        data=_create_brand_form(),
        follow_redirects=False,
    )
    assert response.status_code in (303, 400, 422)
    if response.status_code == 303:
        assert response.headers["location"] == "/brands/coca-cola"
        orders_patch, skus_patch = _mocked_client()
        with orders_patch, skus_patch:
            follow = client.get(response.headers["location"])
        assert "participating SKUs" in follow.text.lower() or "Excel or CSV" in follow.text
    assert persist_store.brands == before
    assert "pepsi" not in persist_store.catalogs
    assert "pepsi" not in persist_store.rewards


def test_create_brand_unparseable_file_does_not_write(persist_store):
    before = dict(persist_store.brands)
    client = TestClient(webapp.app)
    response = client.post(
        "/brands/create",
        data=_create_brand_form(),
        files={"file": ("catalog.xlsx", BytesIO(b"not-an-excel-file"), "application/octet-stream")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert persist_store.brands == before
    assert persist_store.catalogs.get("pepsi") in (None, [])
    assert persist_store.rewards.get("pepsi") in (None, [])
    orders_patch, skus_patch = _mocked_client()
    with orders_patch, skus_patch:
        follow = client.get(response.headers["location"])
    assert follow.status_code == 200
    assert follow.text  # flash present on the page


def test_create_brand_duplicate_name_does_not_overwrite(persist_store):
    persist_store.catalogs["coca-cola"] = [
        {"sku": "SKU-A", "product_title": "Keep Me", "current_points": 50},
    ]
    persist_store.rewards["coca-cola"] = [("8 Dollar Rebate", 5000)]
    client = TestClient(webapp.app)
    response = client.post(
        "/brands/create",
        data=_create_brand_form(label="Coca-Cola"),
        files=_create_catalog_file(),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert persist_store.brands["coca-cola"]["label"] == "Coca-Cola"
    assert persist_store.catalogs["coca-cola"][0]["product_title"] == "Keep Me"
    assert persist_store.rewards["coca-cola"] == [("8 Dollar Rebate", 5000)]
    orders_patch, skus_patch = _mocked_client()
    with orders_patch, skus_patch:
        follow = client.get(response.headers["location"])
    assert "already exists" in follow.text


def test_create_brand_assigns_slug_from_display_name(persist_store):
    orders_patch, skus_patch = _live_catalog(persist_store)
    with orders_patch, skus_patch:
        client = TestClient(webapp.app)
        response = client.post(
            "/brands/create",
            data=_create_brand_form(label="Diet Pepsi", slug="ignored-spoof"),
            files=_create_catalog_file(),
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/brands/diet-pepsi"
        page = client.get("/brands/diet-pepsi")
    assert "diet-pepsi" in persist_store.brands
    assert "ignored-spoof" not in persist_store.brands
    assert page.status_code == 200
    assert "Diet Pepsi" in page.text


def test_download_catalog_template():
    client = TestClient(webapp.app)
    response = client.get("/catalog-template.xlsx")
    assert response.status_code == 200
    assert "spreadsheetml" in response.headers["content-type"]
    df = pd.read_excel(BytesIO(response.content))
    assert list(df.columns)[:3] == ["sku", "product_title", "current_points"]
    assert "size" in df.columns
    assert "brand" in df.columns
    assert "category" in df.columns
    assert len(df) == 0


def test_create_brand_no_reward_does_not_write(persist_store):
    before = dict(persist_store.brands)
    client = TestClient(webapp.app)
    response = client.post(
        "/brands/create",
        data=_create_brand_form(reward_name="", reward_points=""),
        files=_create_catalog_file(),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert persist_store.brands == before
    assert "pepsi" not in persist_store.catalogs
    assert "pepsi" not in persist_store.rewards
    orders_patch, skus_patch = _mocked_client()
    with orders_patch, skus_patch:
        follow = client.get(response.headers["location"])
    assert "at least one reward" in follow.text.lower()


def test_unknown_brand_is_404(persist_store):
    client = TestClient(webapp.app)
    response = client.get("/brands/does-not-exist")
    assert response.status_code == 404
    assert b"Unknown brand" in response.content


def test_pull_order_history_posts_brand_skus_only(persist_store):
    captured = {}

    def fake_refresh(brand, sku_list=None, **kwargs):
        captured["brand"] = brand
        captured["sku_list"] = list(sku_list)
        return {
            "brand": brand,
            "rows": 12,
            "sku_count": len(sku_list),
            "start": date(2026, 3, 1),
            "end": date(2026, 9, 1),
            "windows": [(date(2026, 3, 1), date(2026, 4, 1)), (date(2026, 8, 1), date(2026, 9, 1))],
        }

    orders_patch, skus_patch = _mocked_client()
    with orders_patch, skus_patch, patch.object(webapp, "run_brand_refresh", side_effect=fake_refresh):
        client = TestClient(webapp.app)
        response = client.post("/brands/coca-cola/refresh-orders", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/brands/coca-cola"
        page = client.get(response.headers["location"])
    assert captured["brand"] == "coca-cola"
    assert captured["sku_list"] == ["SKU-A"]
    assert b"Pulled 12 order rows for 1 SKUs" in page.content


def test_pull_order_history_without_skus_does_not_call_athena():
    empty = pd.DataFrame(columns=["sku", "product_title", "current_points"])
    orders_patch, skus_patch = _mocked_client(skus=empty)
    with orders_patch, skus_patch, patch.object(webapp, "run_brand_refresh") as refresh:
        client = TestClient(webapp.app)
        response = client.post("/brands/coca-cola/refresh-orders", follow_redirects=False)
        assert response.status_code == 303
        page = client.get(response.headers["location"])
    refresh.assert_not_called()
    assert b"Add participating SKUs" in page.content


def test_pull_order_history_athena_error_flashes(persist_store):
    orders_patch, skus_patch = _mocked_client()
    with orders_patch, skus_patch, patch.object(
        webapp, "run_brand_refresh", side_effect=RuntimeError("Athena timeout")
    ):
        client = TestClient(webapp.app)
        response = client.post("/brands/coca-cola/refresh-orders", follow_redirects=False)
        page = client.get(response.headers["location"])
    assert b"Could not pull order history: Athena timeout" in page.content
    assert b"application/json" not in page.headers.get("content-type", "").encode()
