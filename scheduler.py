import logging
from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session
from database import engine, Listing, PriceCheck
from plugins import get_plugin

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def check_all_prices():
    with Session(engine) as session:
        listings = session.query(Listing).all()
        for listing in listings:
            try:
                check_listing_prices(listing.id, session)
            except Exception:
                logger.exception("Error checking prices for listing %s", listing.id)
        session.commit()


def check_listing_prices(listing_id: int, session: Session | None = None):
    own_session = session is None
    if own_session:
        session = Session(engine)

    try:
        listing = session.get(Listing, listing_id)
        if not listing:
            return

        plugin = get_plugin(listing.site)
        if not plugin:
            return

        now = datetime.now(timezone.utc)
        listing.last_checked_at = now

        raw = plugin.fetch_product(listing.handle)
        if not raw:
            logger.warning("Failed to fetch listing %s from %s", listing.handle, listing.site)
            return

        data = plugin.parse_product(raw)

        variant_map = {v.external_variant_id: v for v in listing.variants}

        seen_external_ids = set()
        for v_data in data["variants"]:
            seen_external_ids.add(v_data["external_variant_id"])
            variant = variant_map.get(v_data["external_variant_id"])
            if variant and variant.tracked:
                last = (
                    session.query(PriceCheck)
                    .filter_by(variant_id=variant.id)
                    .order_by(PriceCheck.checked_at.desc())
                    .first()
                )
                price_same = (
                    last
                    and last.price == v_data["price"]
                    and last.compare_at_price == v_data["compare_at_price"]
                )
                stock_same = not plugin.tracks_stock or (last and last.in_stock == v_data["in_stock"])
                if price_same and stock_same:
                    continue
                check = PriceCheck(
                    variant_id=variant.id,
                    price=v_data["price"],
                    compare_at_price=v_data["compare_at_price"],
                    in_stock=v_data["in_stock"],
                    checked_at=now,
                )
                session.add(check)

        if plugin.tracks_stock:
            for variant in listing.variants:
                if not variant.tracked or variant.external_variant_id in seen_external_ids:
                    continue
                last = (
                    session.query(PriceCheck)
                    .filter_by(variant_id=variant.id)
                    .order_by(PriceCheck.checked_at.desc())
                    .first()
                )
                if last and last.in_stock:
                    session.add(PriceCheck(
                        variant_id=variant.id,
                        price=last.price,
                        compare_at_price=last.compare_at_price,
                        in_stock=False,
                        checked_at=now,
                    ))

        if own_session:
            session.commit()
    finally:
        if own_session:
            session.close()


def start_scheduler(interval_hours: int = 1):
    scheduler.add_job(
        check_all_prices,
        "interval",
        hours=interval_hours,
        id="price_check",
        replace_existing=True,
    )
    scheduler.start()


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()
