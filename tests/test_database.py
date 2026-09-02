import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from database import Base, get_db, init_db


@pytest.fixture
def mem_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def tmp_db_path(tmp_path):
    return tmp_path / "test.db"


class TestGetDb:
    def test_yields_a_usable_session(self, mem_engine):
        with patch("database.engine", mem_engine):
            gen = get_db()
            session = next(gen)
            assert session is not None
            assert isinstance(session, Session)
            try:
                next(gen)
            except StopIteration:
                pass


class TestInitDbFreshInstall:
    def test_creates_new_shape_tables(self, tmp_db_path):
        engine = create_engine(f"sqlite:///{tmp_db_path}", connect_args={"check_same_thread": False})
        with patch("database.engine", engine), patch("database.DATABASE_URL", f"sqlite:///{tmp_db_path}"):
            init_db()

        insp = inspect(engine)
        tables = insp.get_table_names()
        assert "products" in tables
        assert "listings" in tables
        assert "variants" in tables
        assert "price_checks" in tables

        product_cols = {c["name"] for c in insp.get_columns("products")}
        assert "slug" in product_cols
        assert "handle" not in product_cols

        listing_cols = {c["name"] for c in insp.get_columns("listings")}
        assert {"site", "handle", "product_url"} <= listing_cols

        variant_cols = {c["name"] for c in insp.get_columns("variants")}
        assert "listing_id" in variant_cols
        assert "product_id" not in variant_cols

    def test_is_idempotent(self, tmp_db_path):
        engine = create_engine(f"sqlite:///{tmp_db_path}", connect_args={"check_same_thread": False})
        with patch("database.engine", engine), patch("database.DATABASE_URL", f"sqlite:///{tmp_db_path}"):
            init_db()
            init_db()  # should not raise on a DB already at head


class TestInitDbLegacyUpgrade:
    def _seed_legacy_schema(self, db_path: Path):
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE products (
                id INTEGER PRIMARY KEY,
                handle VARCHAR NOT NULL UNIQUE,
                title VARCHAR NOT NULL,
                image_url VARCHAR,
                product_url VARCHAR NOT NULL,
                site VARCHAR NOT NULL DEFAULT 'mepsking',
                created_at DATETIME,
                last_checked_at DATETIME
            );
            CREATE TABLE variants (
                id INTEGER PRIMARY KEY,
                product_id INTEGER NOT NULL REFERENCES products(id),
                external_variant_id VARCHAR NOT NULL,
                name VARCHAR NOT NULL,
                sku VARCHAR,
                tracked BOOLEAN,
                created_at DATETIME
            );
            CREATE TABLE price_checks (
                id INTEGER PRIMARY KEY,
                variant_id INTEGER NOT NULL REFERENCES variants(id),
                price FLOAT NOT NULL,
                compare_at_price FLOAT,
                in_stock BOOLEAN NOT NULL DEFAULT 1,
                checked_at DATETIME
            );
            INSERT INTO products (id, handle, title, image_url, product_url, site, created_at, last_checked_at)
            VALUES (1, 'test-motor', 'Test FPV Motor', 'http://img/1.jpg', 'http://x/test-motor.html', 'mepsking', '2026-01-01', '2026-01-02');
            INSERT INTO variants (id, product_id, external_variant_id, name, sku, tracked, created_at)
            VALUES (10, 1, 'v1', '1900KV', 'SKU1', 1, '2026-01-01');
            INSERT INTO price_checks (id, variant_id, price, compare_at_price, in_stock, checked_at)
            VALUES (100, 10, 16.9, 26.9, 1, '2026-01-01');
            """
        )
        conn.commit()
        conn.close()

    def test_migrates_legacy_data_into_listings(self, tmp_db_path):
        self._seed_legacy_schema(tmp_db_path)
        engine = create_engine(f"sqlite:///{tmp_db_path}", connect_args={"check_same_thread": False})

        with patch("database.engine", engine), patch("database.DATABASE_URL", f"sqlite:///{tmp_db_path}"):
            init_db()

        conn = sqlite3.connect(tmp_db_path)
        conn.row_factory = sqlite3.Row

        product = conn.execute("SELECT * FROM products WHERE id = 1").fetchone()
        assert product["title"] == "Test FPV Motor"
        assert product["slug"]  # generated, non-empty

        listing = conn.execute("SELECT * FROM listings WHERE product_id = 1").fetchone()
        assert listing is not None
        assert listing["site"] == "mepsking"
        assert listing["handle"] == "test-motor"
        assert listing["product_url"] == "http://x/test-motor.html"

        variant = conn.execute("SELECT * FROM variants WHERE id = 10").fetchone()
        assert variant["listing_id"] == listing["id"]

        # price_checks history is untouched
        check = conn.execute("SELECT * FROM price_checks WHERE id = 100").fetchone()
        assert check["variant_id"] == 10
        assert check["price"] == 16.9
        conn.close()
