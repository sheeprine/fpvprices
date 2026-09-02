import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from unittest.mock import patch

from database import Base, get_db, Product, Listing, Variant, PriceCheck
from main import app

# Shared fake product payload (what fetch_product returns — a JSON-LD ProductGroup dict)
FAKE_RAW_PRODUCT = {
    "@context": "https://schema.org/",
    "@type": "ProductGroup",
    "name": "Test FPV Motor",
    "url": "https://www.mepsking.shop/test-motor.html",
    "productGroupID": "9999999999999999999",
    "hasVariant": [
        {
            "@type": "Product",
            "sku": "1111111111111111111",
            "productId": "TEST-1900",
            "name": "Test FPV Motor-1900KV / Blue",
            "image": ["https://img-meps.mepsking.top/material/1/test-motor.jpg"],
            "offers": {
                "@type": "Offer",
                "url": "https://www.mepsking.shop/test-motor.html?spec=1900KV",
                "priceSpecification": [
                    {"@type": "UnitPriceSpecification", "price": 16.90, "priceCurrency": "USD"},
                    {"@type": "UnitPriceSpecification", "priceType": "https://schema.org/StrikethroughPrice", "price": 26.90, "priceCurrency": "USD"},
                ],
            },
        },
        {
            "@type": "Product",
            "sku": "2222222222222222222",
            "productId": "TEST-2500",
            "name": "Test FPV Motor-2500KV / Red",
            "image": ["https://img-meps.mepsking.top/material/1/test-motor.jpg"],
            "offers": {
                "@type": "Offer",
                "url": "https://www.mepsking.shop/test-motor.html?spec=2500KV",
                "priceSpecification": [
                    {"@type": "UnitPriceSpecification", "price": 18.90, "priceCurrency": "USD"},
                    {"@type": "UnitPriceSpecification", "priceType": "https://schema.org/StrikethroughPrice", "price": 28.90, "priceCurrency": "USD"},
                ],
            },
        },
    ],
}


@pytest.fixture
def test_engine():
    # StaticPool ensures all sessions share one in-memory SQLite connection
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session(test_engine):
    with Session(test_engine) as session:
        yield session


@pytest.fixture
def client(test_engine):
    def override_get_db():
        with Session(test_engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    with (
        patch("main.init_db"),
        patch("main.start_scheduler"),
        patch("main.stop_scheduler"),
    ):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c

    app.dependency_overrides.clear()


@pytest.fixture
def admin_client(client):
    with patch.dict(os.environ, {"ADMIN_PASSWORD": "test-secret"}):
        resp = client.post(
            "/admin/login",
            data={"password": "test-secret", "next": "/add"},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    return client


@pytest.fixture
def seeded_product(db_session):
    product = Product(
        slug="test-motor",
        title="Test FPV Motor",
        image_url="https://img-meps.mepsking.top/material/1/test-motor.jpg",
    )
    db_session.add(product)
    db_session.flush()

    listing = Listing(
        product_id=product.id,
        site="mepsking",
        handle="test-motor",
        product_url="https://www.mepsking.shop/test-motor.html",
        image_url="https://img-meps.mepsking.top/material/1/test-motor.jpg",
    )
    db_session.add(listing)
    db_session.flush()

    v1 = Variant(listing_id=listing.id, external_variant_id="1111111111111111111", name="1900KV / Blue", sku="TEST-1900", tracked=True)
    v2 = Variant(listing_id=listing.id, external_variant_id="2222222222222222222", name="2500KV / Red", sku="TEST-2500", tracked=True)
    db_session.add_all([v1, v2])
    db_session.flush()

    db_session.add_all([
        PriceCheck(variant_id=v1.id, price=16.90, compare_at_price=26.90),
        PriceCheck(variant_id=v2.id, price=18.90, compare_at_price=28.90),
    ])
    db_session.commit()

    return product
