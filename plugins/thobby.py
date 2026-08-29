import json
import re
from typing import Optional

import httpx
from plugins import SitePlugin

THOBBY_BASE = "https://www.t-hobby.com"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}

_PRODUCT_JSON_RE = re.compile(
    r'<script[^>]*id="product-json"[^>]*>(.*?)</script>', re.DOTALL
)


class THobbyPlugin(SitePlugin):
    name = "thobby"
    display_name = "T-Hobby"
    currency = "$"
    tracks_stock = True

    def can_handle(self, url: str) -> bool:
        return "t-hobby.com" in url

    def extract_handle(self, url: str) -> Optional[str]:
        match = re.search(r"/products/([^/?#]+)", url)
        return match.group(1) if match else None

    def fetch_product(self, handle: str) -> Optional[dict]:
        url = f"{THOBBY_BASE}/products/{handle}"
        try:
            with httpx.Client(headers=_HEADERS, follow_redirects=True, timeout=15) as client:
                resp = client.get(url)
                if resp.status_code != 200:
                    return None
                match = _PRODUCT_JSON_RE.search(resp.text)
                if not match:
                    return None
                data = json.loads(match.group(1))
                return data.get("product")
        except Exception:
            return None

    def parse_product(self, raw: dict) -> dict:
        image_url = None
        image = raw.get("image")
        if image and image.get("src"):
            src = image["src"]
            image_url = f"https:{src}" if src.startswith("//") else src

        variants = []
        for v in raw.get("variants", []):
            price = float(v["price"]) if v.get("price") else 0.0
            compare_at = float(v["compare_at_price"]) if v.get("compare_at_price") else None
            variants.append({
                "external_variant_id": str(v["id"]),
                "name": v.get("title", "Default"),
                "sku": v.get("sku", ""),
                "price": price,
                "compare_at_price": compare_at,
                "in_stock": bool(v.get("available", False)),
            })

        return {
            "handle": raw["handle"],
            "title": raw["title"],
            "image_url": image_url,
            "product_url": f"{THOBBY_BASE}/products/{raw['handle']}",
            "variants": variants,
        }
