"""Baseline: schema as it existed before multi-shop listings.

Revision ID: 0001
Revises:
Create Date: 2026-09-02
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("handle", sa.String, nullable=False, unique=True),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("image_url", sa.String),
        sa.Column("product_url", sa.String, nullable=False),
        sa.Column("site", sa.String, nullable=False, server_default="mepsking"),
        sa.Column("created_at", sa.DateTime),
        sa.Column("last_checked_at", sa.DateTime),
    )
    op.create_table(
        "variants",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("product_id", sa.Integer, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("external_variant_id", sa.String, nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("sku", sa.String),
        sa.Column("tracked", sa.Boolean, default=True),
        sa.Column("created_at", sa.DateTime),
    )
    op.create_table(
        "price_checks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("variant_id", sa.Integer, sa.ForeignKey("variants.id"), nullable=False),
        sa.Column("price", sa.Float, nullable=False),
        sa.Column("compare_at_price", sa.Float),
        sa.Column("in_stock", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("checked_at", sa.DateTime),
    )


def downgrade() -> None:
    op.drop_table("price_checks")
    op.drop_table("variants")
    op.drop_table("products")
