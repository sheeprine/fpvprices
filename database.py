from datetime import datetime, timezone
from pathlib import Path
import os
import re


def _utcnow():
    return datetime.now(timezone.utc)
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean, DateTime, ForeignKey,
    UniqueConstraint, inspect
)
from sqlalchemy.orm import DeclarativeBase, relationship, Session

from alembic import command
from alembic.config import Config

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./fpvprices.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


class Base(DeclarativeBase):
    pass


class Product(Base):
    """A logical item, possibly sold on several sites (see Listing)."""
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    slug = Column(String, unique=True, nullable=False)
    title = Column(String, nullable=False)
    image_url = Column(String)
    created_at = Column(DateTime, default=_utcnow)

    listings = relationship("Listing", back_populates="product", cascade="all, delete-orphan")


class Listing(Base):
    """One site's listing for a Product — carries the site-specific handle/URL/variants."""
    __tablename__ = "listings"
    __table_args__ = (UniqueConstraint("site", "handle", name="uq_listings_site_handle"),)

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    site = Column(String, nullable=False)
    handle = Column(String, nullable=False)
    product_url = Column(String, nullable=False)
    image_url = Column(String)
    created_at = Column(DateTime, default=_utcnow)
    last_checked_at = Column(DateTime)

    product = relationship("Product", back_populates="listings")
    variants = relationship("Variant", back_populates="listing", cascade="all, delete-orphan")


class Variant(Base):
    __tablename__ = "variants"

    id = Column(Integer, primary_key=True)
    listing_id = Column(Integer, ForeignKey("listings.id"), nullable=False)
    external_variant_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    sku = Column(String)
    tracked = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)

    listing = relationship("Listing", back_populates="variants")
    price_checks = relationship("PriceCheck", back_populates="variant", cascade="all, delete-orphan")


class PriceCheck(Base):
    __tablename__ = "price_checks"

    id = Column(Integer, primary_key=True)
    variant_id = Column(Integer, ForeignKey("variants.id"), nullable=False)
    price = Column(Float, nullable=False)
    compare_at_price = Column(Float)
    in_stock = Column(Boolean, nullable=False, default=True, server_default="1")
    checked_at = Column(DateTime, default=_utcnow)

    variant = relationship("Variant", back_populates="price_checks")


def get_db():
    with Session(engine) as session:
        yield session


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "item"


def generate_unique_slug(session: Session, title: str) -> str:
    base = _slugify(title)
    slug = base
    n = 2
    while session.query(Product).filter_by(slug=slug).first() is not None:
        slug = f"{base}-{n}"
        n += 1
    return slug


_ALEMBIC_INI = Path(__file__).parent / "alembic.ini"


def _alembic_config() -> Config:
    return Config(str(_ALEMBIC_INI))


def init_db():
    """Bring the database up to the latest Alembic revision.

    A database that predates Alembic (tables already exist, but there's no
    alembic_version table yet) is stamped to the pre-refactor baseline
    revision first, so it replays only the migrations it actually needs.
    """
    cfg = _alembic_config()
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    if "alembic_version" not in tables and "products" in tables:
        product_cols = {col["name"] for col in inspector.get_columns("products")}
        if "handle" in product_cols:
            command.stamp(cfg, "0001")
    command.upgrade(cfg, "head")
