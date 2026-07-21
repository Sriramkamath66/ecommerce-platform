from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from tests.conftest import FAKE_VECTOR, make_product_payload


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health(async_client: AsyncClient):
    resp = await async_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "service" in body


# ---------------------------------------------------------------------------
# Category CRUD
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_category(async_client: AsyncClient):
    resp = await async_client.post(
        "/categories/",
        json={"name": "Electronics", "slug": "electronics"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Electronics"
    assert body["slug"] == "electronics"
    assert "id" in body


@pytest.mark.asyncio
async def test_list_categories_empty(async_client: AsyncClient):
    resp = await async_client.get("/categories/")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_get_category_not_found(async_client: AsyncClient):
    resp = await async_client.get(f"/categories/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_category(async_client: AsyncClient):
    # Create first
    resp = await async_client.post(
        "/categories/",
        json={"name": "Books", "slug": "books"},
    )
    assert resp.status_code == 201
    cat_id = resp.json()["id"]

    # Update
    resp = await async_client.patch(
        f"/categories/{cat_id}",
        json={"description": "All kinds of books"},
    )
    assert resp.status_code == 200
    assert resp.json()["description"] == "All kinds of books"


@pytest.mark.asyncio
async def test_delete_category(async_client: AsyncClient):
    resp = await async_client.post(
        "/categories/",
        json={"name": "ToDelete", "slug": "to-delete"},
    )
    assert resp.status_code == 201
    cat_id = resp.json()["id"]

    resp = await async_client.delete(f"/categories/{cat_id}")
    assert resp.status_code == 204

    # Confirm it's gone
    resp = await async_client.get(f"/categories/{cat_id}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Product CRUD
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_product(async_client: AsyncClient, mock_embedding_service, mock_qdrant_service):
    payload = make_product_payload()
    resp = await async_client.post("/products/", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == payload["name"]
    assert body["sku"] == payload["sku"]
    assert body["is_active"] is True
    assert len(body["images"]) == 1

    # Embedding and Qdrant upsert should have been called
    mock_embedding_service.embed_product.assert_called_once()
    mock_qdrant_service.upsert_product.assert_called_once()


@pytest.mark.asyncio
async def test_create_product_with_category(async_client: AsyncClient):
    # Create category first
    cat_resp = await async_client.post(
        "/categories/",
        json={"name": "Gadgets", "slug": "gadgets"},
    )
    assert cat_resp.status_code == 201
    cat_id = cat_resp.json()["id"]

    payload = make_product_payload(category_id=cat_id)
    resp = await async_client.post("/products/", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["category_id"] == cat_id
    assert body["category_name"] == "Gadgets"


@pytest.mark.asyncio
async def test_get_product(async_client: AsyncClient):
    payload = make_product_payload()
    create_resp = await async_client.post("/products/", json=payload)
    assert create_resp.status_code == 201
    product_id = create_resp.json()["id"]

    resp = await async_client.get(f"/products/{product_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == product_id


@pytest.mark.asyncio
async def test_get_product_not_found(async_client: AsyncClient):
    resp = await async_client.get(f"/products/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_products_empty(async_client: AsyncClient):
    resp = await async_client.get("/products/")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "total" in body
    assert body["page"] == 1


@pytest.mark.asyncio
async def test_list_products_pagination(async_client: AsyncClient):
    # Create 3 products
    for i in range(3):
        p = make_product_payload(name=f"Product {i}", sku=f"SKU-PAGE-{i:04d}")
        resp = await async_client.post("/products/", json=p)
        assert resp.status_code == 201

    resp = await async_client.get("/products/?page=1&page_size=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["page_size"] == 2


@pytest.mark.asyncio
async def test_update_product(async_client: AsyncClient, mock_embedding_service):
    payload = make_product_payload()
    create_resp = await async_client.post("/products/", json=payload)
    product_id = create_resp.json()["id"]
    mock_embedding_service.embed_product.reset_mock()

    resp = await async_client.patch(
        f"/products/{product_id}",
        json={"price": "29.99", "name": "Updated Widget"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Updated Widget"
    assert float(body["price"]) == pytest.approx(29.99, rel=1e-3)

    # Re-embedding should have been triggered because name changed
    mock_embedding_service.embed_product.assert_called()


@pytest.mark.asyncio
async def test_update_product_not_found(async_client: AsyncClient):
    resp = await async_client.patch(
        f"/products/{uuid.uuid4()}",
        json={"price": "9.99"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_soft_delete_product(async_client: AsyncClient, mock_qdrant_service):
    payload = make_product_payload()
    create_resp = await async_client.post("/products/", json=payload)
    product_id = create_resp.json()["id"]
    mock_qdrant_service.delete_product.reset_mock()

    # Delete
    del_resp = await async_client.delete(f"/products/{product_id}")
    assert del_resp.status_code == 204

    # Qdrant delete should have been called
    mock_qdrant_service.delete_product.assert_called_once()

    # Product should no longer appear in active listing
    list_resp = await async_client.get("/products/?is_active=true")
    ids = [p["id"] for p in list_resp.json()["items"]]
    assert product_id not in ids


@pytest.mark.asyncio
async def test_delete_product_not_found(async_client: AsyncClient):
    resp = await async_client.delete(f"/products/{uuid.uuid4()}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Semantic search (Qdrant + VoyageAI mocked)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_products_empty_results(
    async_client: AsyncClient,
    mock_qdrant_service,
    mock_embedding_service,
):
    """When Qdrant returns no hits, the endpoint returns an empty list."""
    mock_qdrant_service.search_products.return_value = []

    resp = await async_client.get("/products/search", params={"q": "wireless headphones"})
    assert resp.status_code == 200
    assert resp.json() == []
    mock_embedding_service.embed_text.assert_called_once_with("wireless headphones")


@pytest.mark.asyncio
async def test_search_products_with_results(
    async_client: AsyncClient,
    mock_qdrant_service,
    mock_embedding_service,
):
    """Search returns matching products fetched from DB."""
    # Create a product first
    payload = make_product_payload(name="Wireless Headphones", sku="SKU-WH-001")
    create_resp = await async_client.post("/products/", json=payload)
    assert create_resp.status_code == 201
    product_id = create_resp.json()["id"]

    # Make Qdrant return this product as a hit
    mock_qdrant_service.search_products.return_value = [
        {"id": product_id, "score": 0.95, "payload": {"name": "Wireless Headphones"}}
    ]

    resp = await async_client.get("/products/search", params={"q": "wireless headphones", "limit": 5})
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 1
    assert results[0]["score"] == pytest.approx(0.95, rel=1e-3)
    assert results[0]["product"]["id"] == product_id


@pytest.mark.asyncio
async def test_search_products_filters_passed_to_qdrant(
    async_client: AsyncClient,
    mock_qdrant_service,
    mock_embedding_service,
):
    """Verify that filter params are forwarded to the Qdrant search call."""
    mock_qdrant_service.search_products.return_value = []

    cat_id = str(uuid.uuid4())
    resp = await async_client.get(
        "/products/search",
        params={"q": "test", "category_id": cat_id, "min_price": "10", "max_price": "100"},
    )
    assert resp.status_code == 200

    # search_products(query_vector, limit, filters) — third positional arg
    call_args = mock_qdrant_service.search_products.call_args
    pos_args = call_args[0]  # positional tuple
    kw_args = call_args[1]   # keyword dict
    filters = pos_args[2] if len(pos_args) > 2 else kw_args.get("filters")
    assert filters is not None
    assert str(filters.get("category_id")) == cat_id


# ---------------------------------------------------------------------------
# Filter tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_products_filter_by_price(async_client: AsyncClient):
    p1 = make_product_payload(name="Cheap", sku="SKU-CHEAP-001", price="5.00")
    p2 = make_product_payload(name="Expensive", sku="SKU-EXP-001", price="500.00")
    await async_client.post("/products/", json=p1)
    await async_client.post("/products/", json=p2)

    resp = await async_client.get("/products/?max_price=10")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(float(i["price"]) <= 10 for i in items)

    resp = await async_client.get("/products/?min_price=100")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(float(i["price"]) >= 100 for i in items)


@pytest.mark.asyncio
async def test_list_products_filter_by_category(async_client: AsyncClient):
    cat_resp = await async_client.post(
        "/categories/",
        json={"name": "Clothing", "slug": "clothing"},
    )
    cat_id = cat_resp.json()["id"]

    p_in = make_product_payload(name="T-Shirt", sku="SKU-TS-001", category_id=cat_id)
    p_out = make_product_payload(name="Laptop", sku="SKU-LP-001")
    await async_client.post("/products/", json=p_in)
    await async_client.post("/products/", json=p_out)

    resp = await async_client.get(f"/products/?category_id={cat_id}")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["name"] == "T-Shirt"
