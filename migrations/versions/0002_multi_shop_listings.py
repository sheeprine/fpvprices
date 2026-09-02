"""Split Product into Product (item) + Listing (site-specific).

Every pre-migration product implicitly represented exactly one listing, so
the new listing for a product reuses that product's own id — meaning
variants.product_id values are already correct listings.id values and need
no remapping.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-02
"""
import re

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "item"


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "listings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("product_id", sa.Integer, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("site", sa.String, nullable=False),
        sa.Column("handle", sa.String, nullable=False),
        sa.Column("product_url", sa.String, nullable=False),
        sa.Column("image_url", sa.String),
        sa.Column("created_at", sa.DateTime),
        sa.Column("last_checked_at", sa.DateTime),
        sa.UniqueConstraint("site", "handle", name="uq_listings_site_handle"),
    )

    op.execute(
        "INSERT INTO listings "
        "(id, product_id, site, handle, product_url, image_url, created_at, last_checked_at) "
        "SELECT id, id, site, handle, product_url, image_url, created_at, last_checked_at "
        "FROM products"
    )

    with op.batch_alter_table("variants") as batch_op:
        batch_op.add_column(sa.Column("listing_id", sa.Integer))

    op.execute("UPDATE variants SET listing_id = product_id")

    with op.batch_alter_table("variants") as batch_op:
        batch_op.alter_column("listing_id", nullable=False)
        batch_op.drop_column("product_id")
        batch_op.create_foreign_key(
            "fk_variants_listing_id_listings", "listings", ["listing_id"], ["id"]
        )

    with op.batch_alter_table("products") as batch_op:
        batch_op.add_column(sa.Column("slug", sa.String))

    rows = bind.execute(sa.text("SELECT id, title FROM products")).fetchall()
    used_slugs = set()
    for row in rows:
        base = _slugify(row.title)
        slug = base
        n = 2
        while slug in used_slugs:
            slug = f"{base}-{n}"
            n += 1
        used_slugs.add(slug)
        bind.execute(sa.text("UPDATE products SET slug = :slug WHERE id = :id"), {"slug": slug, "id": row.id})

    with op.batch_alter_table("products") as batch_op:
        batch_op.alter_column("slug", nullable=False)
        batch_op.create_unique_constraint("uq_products_slug", ["slug"])
        batch_op.drop_column("handle")
        batch_op.drop_column("site")
        batch_op.drop_column("product_url")
        batch_op.drop_column("last_checked_at")


def downgrade() -> None:
    bind = op.get_bind()

    dup = bind.execute(sa.text(
        "SELECT product_id FROM listings GROUP BY product_id HAVING COUNT(*) > 1"
    )).fetchall()
    if dup:
        raise RuntimeError(
            "Cannot downgrade: some products have more than one listing "
            "(merging them back into a single-listing product would lose data)."
        )

    with op.batch_alter_table("products") as batch_op:
        batch_op.add_column(sa.Column("handle", sa.String))
        batch_op.add_column(sa.Column("site", sa.String))
        batch_op.add_column(sa.Column("product_url", sa.String))
        batch_op.add_column(sa.Column("last_checked_at", sa.DateTime))

    op.execute(
        "UPDATE products SET "
        "handle = (SELECT handle FROM listings WHERE listings.product_id = products.id), "
        "site = (SELECT site FROM listings WHERE listings.product_id = products.id), "
        "product_url = (SELECT product_url FROM listings WHERE listings.product_id = products.id), "
        "last_checked_at = (SELECT last_checked_at FROM listings WHERE listings.product_id = products.id)"
    )

    with op.batch_alter_table("variants") as batch_op:
        batch_op.add_column(sa.Column("product_id", sa.Integer))

    op.execute("UPDATE variants SET product_id = listing_id")

    with op.batch_alter_table("variants") as batch_op:
        batch_op.alter_column("product_id", nullable=False)
        batch_op.drop_column("listing_id")
        batch_op.create_foreign_key(
            "fk_variants_product_id_products", "products", ["product_id"], ["id"]
        )

    with op.batch_alter_table("products") as batch_op:
        batch_op.alter_column("handle", nullable=False)
        batch_op.alter_column("product_url", nullable=False)
        batch_op.alter_column("site", nullable=False, server_default="mepsking")
        batch_op.create_unique_constraint("uq_products_handle", ["handle"])
        batch_op.drop_column("slug")

    op.drop_table("listings")
