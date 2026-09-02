# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Install with dev dependencies (pytest)
uv sync --group dev

# Run the app
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_scraper.py

# Run a single test
uv run pytest tests/test_routes.py::TestTrack::test_creates_product_and_listing_in_db

# Create a new schema migration (see Schema migrations below)
uv run alembic revision -m "describe the change"
```

## Architecture

The app tracks FPV gear prices across multiple e-commerce sites, and the same physical item can be tracked on several sites at once. Each site is handled by a **plugin** (`plugins/`) that implements scraping, URL parsing, and site-specific behaviour. A shared core handles routes, scheduling, templates, and the database.

`Product` is the logical item (title/image only); each site it's sold on gets its own `Listing` row (handle, URL, its own variants/price history) — see **Data model** below.

**Request flow for tracking a product:**
1. User pastes a URL → `POST /lookup` calls `get_plugin_for_url()` to find the right plugin, extracts the handle, fetches and parses the product, renders `preview.html` with variant checkboxes. If a `Listing` for that `(site, handle)` already exists, the preview notes which item it's already part of; otherwise the preview offers an "attach to existing item" select built from all tracked `Product`s.
2. User selects variants → `POST /track` receives the `site` form field and optional `item_id`, re-fetches via the plugin, and resolves the target `Product`: reuse the existing `Listing`'s product if `(site, handle)` is already tracked, else attach a new `Listing` to `item_id` if given, else create a new `Product` + `Listing`. Saves `Variant` rows and records the first `PriceCheck`.
3. `APScheduler` runs `check_all_prices()` every hour, iterating `Listing` rows; `check_listing_prices()` looks up the listing's plugin via `listing.site`, skips writing a `PriceCheck` when price, `compare_at_price`, and (if `plugin.tracks_stock`) `in_stock` are all unchanged.
4. If the same item turns out to be tracked twice under separate `Product`s, `POST /products/{slug}/merge` reparents all of one product's listings onto another and deletes the now-empty source.

**Plugin system** (`plugins/`):
- `plugins/__init__.py` — `SitePlugin` ABC + registry (`register`, `get_plugin`, `get_plugin_for_url`)
- `plugins/mepsking.py` — `MepskingPlugin`: scrapes JSON-LD `ProductGroup` from HTML, `currency="$"`, `tracks_stock=True`
- `plugins/ampow.py` — `AmpowPlugin`: calls Shopify JSON API (`/products/{handle}.json`), `currency="€"`, `tracks_stock=False`
- `scraper.py` — thin backward-compat wrapper delegating to `MepskingPlugin`; kept so existing scraper tests don't need changes

**Adding a new site:** create `plugins/<site>.py` with a class extending `SitePlugin`, implement `can_handle`, `extract_handle`, `fetch_product`, `parse_product`, and optionally override `extract_pack_count`. Register the instance at the bottom of `plugins/__init__.py`.

**MepsKing scraper strategy** (`plugins/mepsking.py`):
- Product pages are at `https://www.mepsking.shop/{handle}.html`
- Each page embeds a JSON-LD `ProductGroup` block containing `hasVariant` — an array of individual `Product` entries
- Prices are in `offers.priceSpecification`: entries without `priceType` are the sale price; entries with `priceType == "https://schema.org/StrikethroughPrice"` are the original/RRP price
- Variant names are stored with the product group name as a prefix (e.g. `"Test Motor-1900KV / Blue"`); `parse_product()` strips this prefix so only the variant-specific part is saved
- The `sku` field in JSON-LD is a large numeric string used as `external_variant_id`; the `productId` field is the human-readable SKU (e.g. `AMD01020003`)

**Ampow scraper strategy** (`plugins/ampow.py`):
- Shopify store; fetches `https://www.ampow.com/products/{handle}.json`
- Variant IDs are Shopify integers, stored as strings in `external_variant_id`
- No stock availability in the API; `in_stock` is always stored as `True`

**Admin protection:**
- Write actions (`/lookup`, `/track`, `POST /products/{slug}/check|delete|merge`, `POST /listings/{id}/check|delete`) require the session to have `is_admin = True`
- Set `ADMIN_PASSWORD` env var to enable the login wall; `GET /admin/login` and `POST /admin/login` handle auth; `POST /admin/logout` clears the session
- `SESSION_SECRET` env var controls the session signing key (auto-generated random value if unset)

**Data model** (`database.py`):
- `Product` — one row per logical item; `slug` (unique, generated from title at creation, never changed) is the URL key used by `/products/{slug}`
- `Listing` — one row per site a `Product` is sold on; holds `site`, `handle`, `product_url`, and its own `image_url`/`last_checked_at`. `(site, handle)` is unique — this is the old `Product`'s natural key, now scoped per-listing instead of globally
- `Variant` — one row per tracked variant, FK'd to `Listing` (not `Product`); `external_variant_id` stores the site-specific variant ID as a string; `sku` stores the human-readable SKU; `tracked=False` soft-disables without deleting history
- `PriceCheck` — append-only price snapshot; `in_stock` is `True` for plugins that don't track stock

**Key design decisions:**
- `external_variant_id` is `String` (not Integer) to hold both large numeric mepsking SKUs and Shopify integer IDs
- Plugin currency (`plugin.currency`) and stock-column visibility (`plugin.tracks_stock`) are resolved at render time from `listing.site`
- `extract_pack_count()` is plugin-specific: mepsking matches explicit `Npcs`/`Npack` patterns only (avoids matching KV ratings like "1900KV"); ampow matches the first number in the name
- `time_ago()` in `main.py` normalises naive datetimes to UTC before comparing — necessary because SQLite strips timezone info on read
- The price history chart on `/products/{slug}` combines every listing's variants into one Chart.js chart, one dataset per `(listing, variant)` labeled `"{plugin.display_name}: {variant.name}"`. Because listings can be priced in different currencies, the y-axis has no currency prefix — each dataset carries its own `currency` for the tooltip instead. Uses `stepped: 'before'` so flat price periods render as horizontal steps; the JS carries the last known price forward and always appends a synthetic "now" label
- Time range buttons (15d / 1m / 3m / 6m / All) filter labels client-side
- Stock-based dashed-line rendering just checks each dataset's own `in_stock` history — plugins that don't track stock never record `in_stock=False`, so the dash logic is naturally inert for them without needing a separate flag
- Merging (`POST /products/{slug}/merge`) must reassign listings via the relationship collection (`target.listings.append(listing)`), not by setting `listing.product_id` directly — cascade `delete-orphan` tracks collection membership in the session, so a raw FK assignment still gets the listing deleted when the source `Product` is deleted

**Schema migrations — Alembic** (`migrations/`):
- Schema changes are Alembic revisions under `migrations/versions/`, not hand-rolled `ALTER TABLE` checks. Create one with `uv run alembic revision -m "..."` (or `--autogenerate`, then review — autogenerate doesn't know about data-preserving copy steps like the `Product`→`Listing` split in `0002_multi_shop_listings.py`)
- `migrations/env.py` always targets `database.DATABASE_URL` (ignores the placeholder URL in `alembic.ini`), so it points at whatever the app is actually configured to use
- `database.py`'s `init_db()` (called from `main.py`'s `lifespan`, same as before) runs `alembic upgrade head` on startup. A database that predates Alembic (tables exist, no `alembic_version` table) is stamped to the `0001` baseline revision first, so it only replays the migrations it actually needs — there's no manual migration step in the deploy pipeline, so this must stay automatic
- SQLite can't drop/rename columns directly — migrations that need to use `op.batch_alter_table(...)`, which recreates the table under the hood

**Test setup** (`tests/conftest.py`):
- Uses `StaticPool` so all SQLAlchemy sessions share one in-memory SQLite connection within a test
- `client` fixture patches `main.init_db`, `main.start_scheduler`, and `main.stop_scheduler` to avoid touching the production DB or starting background jobs
- `get_db` dependency is overridden via `app.dependency_overrides` to use the test engine
- `FAKE_RAW_PRODUCT` is a JSON-LD `ProductGroup` dict — the format `MepskingPlugin.fetch_product()` returns; a fake Shopify-shaped payload for `AmpowPlugin` lives locally in `test_routes.py` (`FAKE_RAW_AMPOW`) since the two plugins expect different raw shapes
- Plugin methods are mocked via `patch.object(get_plugin("mepsking"), "fetch_product", ...)` rather than patching module-level names; `/track` form data must include `site="mepsking"`
- `seeded_product` fixture returns a `Product` with one `Listing` (accessible via `seeded_product.listings[0]`) and two tracked `Variant`s on that listing

**Starlette 1.3.x API note:** `TemplateResponse` takes `request` as the first positional argument, not inside the context dict:
```python
# correct
templates.TemplateResponse(request, "template.html", {"key": val})
```
