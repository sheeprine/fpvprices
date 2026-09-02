from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional
import json
import os
import secrets

from fastapi import FastAPI, Request, Form, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased
from starlette.middleware.sessions import SessionMiddleware

from database import init_db, get_db, Product, Variant, PriceCheck
from plugins import get_plugin_for_url, get_plugin, get_all_plugins
from scheduler import start_scheduler, stop_scheduler, check_product_prices

SESSION_SECRET = os.environ.get("SESSION_SECRET") or secrets.token_hex(32)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler(interval_hours=1)
    yield
    stop_scheduler()


app = FastAPI(title="FPV Prices", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)
templates = Jinja2Templates(directory="templates")


class AdminRequired(Exception):
    pass


_POST_ONLY_SUFFIXES = ("/check", "/delete")

@app.exception_handler(AdminRequired)
async def admin_required_handler(request: Request, exc: AdminRequired):
    path = request.url.path
    for suffix in _POST_ONLY_SUFFIXES:
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return RedirectResponse(f"/admin/login?next={path}", status_code=303)


def require_admin(request: Request) -> None:
    if not request.session.get("is_admin"):
        raise AdminRequired()


def time_ago(dt: Optional[datetime]) -> str:
    if not dt:
        return "never"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    if delta < timedelta(minutes=1):
        return "just now"
    if delta < timedelta(hours=1):
        m = int(delta.total_seconds() / 60)
        return f"{m}m ago"
    if delta < timedelta(days=1):
        h = int(delta.total_seconds() / 3600)
        return f"{h}h ago"
    d = delta.days
    return f"{d}d ago"


templates.env.globals["time_ago"] = time_ago


ACTIVITY_PAGE_SIZE = 10


ACTIVITY_FILTERS = {"all", "price", "stock"}

_PrevCheck = aliased(PriceCheck)
_prev_price_sq = (
    select(_PrevCheck.price)
    .where(_PrevCheck.variant_id == PriceCheck.variant_id, _PrevCheck.id < PriceCheck.id)
    .order_by(_PrevCheck.id.desc())
    .limit(1)
    .correlate(PriceCheck)
    .scalar_subquery()
)
_prev_stock_sq = (
    select(_PrevCheck.in_stock)
    .where(_PrevCheck.variant_id == PriceCheck.variant_id, _PrevCheck.id < PriceCheck.id)
    .order_by(_PrevCheck.id.desc())
    .limit(1)
    .correlate(PriceCheck)
    .scalar_subquery()
)


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    page: int = 1,
    filter: str = Query(default="all", alias="filter"),
    db: Session = Depends(get_db),
):
    products = db.query(Product).order_by(Product.created_at.desc()).all()

    product_summaries = []
    for product in products:
        plugin = get_plugin(product.site or "mepsking")
        tracked = [v for v in product.variants if v.tracked]
        if not tracked:
            continue

        min_price = None
        previous_price = None
        best_per_unit = None
        best_per_unit_variant = None
        has_multi_pack = False
        for variant in tracked:
            checks = (
                db.query(PriceCheck)
                .filter_by(variant_id=variant.id)
                .order_by(PriceCheck.checked_at.asc())
                .all()
            )
            if not checks:
                continue
            latest = checks[-1].price
            previous = checks[-2].price if len(checks) > 1 else None
            pack_count = plugin.extract_pack_count(variant.name)
            if pack_count == 1 and len(tracked) == 1:
                pack_count = plugin.extract_pack_count(product.title)
            if pack_count > 1:
                has_multi_pack = True
            per_unit = latest / pack_count
            if min_price is None or latest < min_price:
                min_price = latest
            if previous is not None and (previous_price is None or previous < previous_price):
                previous_price = previous
            if best_per_unit is None or per_unit < best_per_unit:
                best_per_unit = per_unit
                best_per_unit_variant = variant.name

        change_pct = None
        if min_price is not None and previous_price and previous_price > 0:
            change_pct = round((min_price - previous_price) / previous_price * 100, 1)

        product_summaries.append(
            {
                "product": product,
                "min_price": min_price,
                "change_pct": change_pct,
                "variant_count": len(tracked),
                "best_per_unit": best_per_unit,
                "best_per_unit_variant": best_per_unit_variant,
                "has_multi_pack": has_multi_pack,
                "currency": plugin.currency if plugin else "$",
            }
        )

    filter_type = filter if filter in ACTIVITY_FILTERS else "all"
    base_q = db.query(PriceCheck).join(Variant).join(Product)
    if filter_type == "price":
        base_q = base_q.filter(PriceCheck.price != _prev_price_sq)
    elif filter_type == "stock":
        base_q = base_q.filter(PriceCheck.in_stock != _prev_stock_sq)  # noqa: E712

    total_checks = base_q.count()
    total_pages = max(1, (total_checks + ACTIVITY_PAGE_SIZE - 1) // ACTIVITY_PAGE_SIZE)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * ACTIVITY_PAGE_SIZE

    recent_checks = (
        base_q
        .order_by(PriceCheck.checked_at.desc())
        .offset(offset)
        .limit(ACTIVITY_PAGE_SIZE)
        .all()
    )

    events = []
    for check in recent_checks:
        variant = check.variant
        product = variant.product
        plugin = get_plugin(product.site or "mepsking")
        prev = (
            db.query(PriceCheck)
            .filter(PriceCheck.variant_id == check.variant_id, PriceCheck.id < check.id)
            .order_by(PriceCheck.id.desc())
            .first()
        )
        if prev is None:
            event_type = "started"
        else:
            price_changed = check.price != prev.price
            stock_changed = (plugin and plugin.tracks_stock) and check.in_stock != prev.in_stock
            if price_changed and stock_changed:
                event_type = "price_and_stock"
            elif price_changed:
                event_type = "price"
            else:
                event_type = "stock"
        events.append({
            "check": check,
            "variant": variant,
            "product": product,
            "prev": prev,
            "event_type": event_type,
            "currency": plugin.currency if plugin else "$",
            "tracks_stock": plugin.tracks_stock if plugin else False,
        })

    site_names = {name: p.display_name for name, p in get_all_plugins().items()}
    return templates.TemplateResponse(request, "index.html", {
        "summaries": product_summaries,
        "events": events,
        "page": page,
        "total_pages": total_pages,
        "filter_type": filter_type,
        "site_names": site_names,
    })


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request, next: str = "/add"):
    if not next.startswith("/"):
        next = "/add"
    if not os.environ.get("ADMIN_PASSWORD"):
        request.session["is_admin"] = True
        return RedirectResponse(next, status_code=303)
    return templates.TemplateResponse(request, "admin_login.html", {"next": next})


@app.post("/admin/login")
def admin_login_post(request: Request, password: str = Form(...), next: str = Form(default="/add")):
    if not next.startswith("/"):
        next = "/add"
    admin_password = os.environ.get("ADMIN_PASSWORD")
    if admin_password and password == admin_password:
        request.session["is_admin"] = True
        return RedirectResponse(next, status_code=303)
    return templates.TemplateResponse(
        request, "admin_login.html", {"error": "Invalid password", "next": next}, status_code=401
    )


@app.post("/admin/logout")
def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/add", response_class=HTMLResponse)
def add_page(request: Request, error: Optional[str] = None, _=Depends(require_admin)):
    return templates.TemplateResponse(request, "add.html", {"error": error})


@app.post("/lookup", response_class=HTMLResponse)
def lookup_product(request: Request, url: str = Form(...), _=Depends(require_admin)):
    plugin = get_plugin_for_url(url)
    if not plugin:
        return templates.TemplateResponse(
            request, "add.html",
            {"error": "Unsupported URL. Check that the URL is from a supported site."}
        )

    handle = plugin.extract_handle(url)
    if not handle:
        return templates.TemplateResponse(
            request, "add.html",
            {"error": f"Invalid URL. Could not extract a product handle from that {plugin.display_name} URL."}
        )

    raw = plugin.fetch_product(handle)
    if not raw:
        return templates.TemplateResponse(
            request, "add.html",
            {"error": f"Could not fetch product '{handle}'. Check the URL and try again."}
        )

    product_data = plugin.parse_product(raw)
    return templates.TemplateResponse(request, "preview.html", {"product": product_data, "plugin": plugin})


@app.post("/track")
def track_product(
    request: Request,
    handle: str = Form(...),
    site: str = Form(...),
    variant_ids: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    if not variant_ids:
        return RedirectResponse(f"/add?error=Select+at+least+one+variant", status_code=303)

    plugin = get_plugin(site)
    if not plugin:
        raise HTTPException(status_code=400, detail="Unknown site")

    existing = db.query(Product).filter_by(handle=handle).first()

    raw = plugin.fetch_product(handle)
    if not raw:
        raise HTTPException(status_code=400, detail="Could not fetch product")

    data = plugin.parse_product(raw)

    if not existing:
        product = Product(
            handle=data["handle"],
            title=data["title"],
            image_url=data["image_url"],
            product_url=data["product_url"],
            site=plugin.name,
        )
        db.add(product)
        db.flush()
    else:
        product = existing

    for v_data in data["variants"]:
        if v_data["external_variant_id"] not in variant_ids:
            continue
        existing_variant = (
            db.query(Variant)
            .filter_by(product_id=product.id, external_variant_id=v_data["external_variant_id"])
            .first()
        )
        if not existing_variant:
            variant = Variant(
                product_id=product.id,
                external_variant_id=v_data["external_variant_id"],
                name=v_data["name"],
                sku=v_data["sku"],
                tracked=True,
            )
            db.add(variant)
            db.flush()
            check = PriceCheck(
                variant_id=variant.id,
                price=v_data["price"],
                compare_at_price=v_data["compare_at_price"],
                in_stock=v_data["in_stock"],
            )
            db.add(check)
        else:
            existing_variant.tracked = True

    product.last_checked_at = datetime.now(timezone.utc)
    db.commit()

    return RedirectResponse(f"/products/{handle}", status_code=303)


@app.get("/products/{handle}", response_class=HTMLResponse)
def product_detail(request: Request, handle: str, db: Session = Depends(get_db)):
    product = db.query(Product).filter_by(handle=handle).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    plugin = get_plugin(product.site or "mepsking")
    tracked_variants = [v for v in product.variants if v.tracked]

    variant_data = []
    chart_datasets = []
    all_labels = set()

    for variant in tracked_variants:
        checks = (
            db.query(PriceCheck)
            .filter_by(variant_id=variant.id)
            .order_by(PriceCheck.checked_at.asc())
            .all()
        )
        if not checks:
            continue

        first_price = checks[0].price
        latest_price = checks[-1].price
        previous_price = checks[-2].price if len(checks) > 1 else None
        compare_at = checks[-1].compare_at_price
        in_stock = checks[-1].in_stock
        change_pct = round((latest_price - previous_price) / previous_price * 100, 1) if previous_price else 0
        pack_count = plugin.extract_pack_count(variant.name)
        if pack_count == 1 and len(tracked_variants) == 1:
            pack_count = plugin.extract_pack_count(product.title)

        variant_data.append(
            {
                "variant": variant,
                "first_price": first_price,
                "latest_price": latest_price,
                "compare_at_price": compare_at,
                "in_stock": in_stock,
                "change_pct": change_pct,
                "change_count": sum(1 for i in range(1, len(checks)) if checks[i].price != checks[i-1].price),
                "pack_count": pack_count,
                "price_per_unit": latest_price / pack_count,
            }
        )

        labels = [c.checked_at.strftime("%Y-%m-%d %H:%M") for c in checks]
        prices = [c.price for c in checks]
        stock = [c.in_stock for c in checks]
        for label in labels:
            all_labels.add(label)

        chart_datasets.append(
            {
                "label": variant.name,
                "labels": labels,
                "data": prices,
                "stock": stock,
            }
        )

    sorted_labels = sorted(all_labels)
    has_multi_pack = any(v["pack_count"] > 1 for v in variant_data)

    return templates.TemplateResponse(request, "product.html", {
        "product": product,
        "plugin": plugin,
        "variant_data": variant_data,
        "chart_datasets_json": json.dumps(chart_datasets),
        "chart_labels_json": json.dumps(sorted_labels),
        "has_multi_pack": has_multi_pack,
    })


@app.post("/products/{handle}/check")
def manual_check(handle: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    product = db.query(Product).filter_by(handle=handle).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    check_product_prices(handle)
    return RedirectResponse(f"/products/{handle}", status_code=303)


@app.post("/products/{handle}/delete")
def delete_product(handle: str, db: Session = Depends(get_db), _=Depends(require_admin)):
    product = db.query(Product).filter_by(handle=handle).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    db.delete(product)
    db.commit()
    return RedirectResponse("/", status_code=303)


def main():  # pragma: no cover
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
