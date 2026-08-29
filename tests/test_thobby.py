import json
import httpx
from unittest.mock import MagicMock, patch

from plugins.thobby import THobbyPlugin

plugin = THobbyPlugin()

FAKE_RAW = {
    "handle": "tmotor-its-2306-5-powerful-freestyle-motor-1750kv",
    "title": "T-Motor ITS 2306.5 Powerful Freestyle Motor",
    "image": {"src": "//img.fantaskycdn.com/test-motor.jpeg"},
    "variants": [
        {
            "id": "35ddd7e9-2b6a-419f-a056-e13042272b13",
            "title": "1750KV",
            "sku": "AHD01010017",
            "price": "26.9",
            "compare_at_price": "29.9",
            "available": True,
        },
    ],
}


def _make_html(product: dict) -> str:
    return (
        '<script id="product-json" data-id="abc" type="application/json">'
        f'{json.dumps({"product": product})}'
        "</script>"
    )


class TestTHobbyTracksStock:
    def test_tracks_stock_is_true(self):
        assert plugin.tracks_stock is True


class TestTHobbyCanHandle:
    def test_thobby_url_is_handled(self):
        assert plugin.can_handle("https://www.t-hobby.com/products/test-motor") is True

    def test_mepsking_url_is_not_handled(self):
        assert plugin.can_handle("https://www.mepsking.shop/motor.html") is False

    def test_unrelated_url_is_not_handled(self):
        assert plugin.can_handle("https://www.example.com/shop") is False


class TestTHobbyExtractHandle:
    def test_standard_products_url(self):
        assert plugin.extract_handle("https://www.t-hobby.com/products/test-motor") == "test-motor"

    def test_url_with_query_params(self):
        assert plugin.extract_handle("https://www.t-hobby.com/products/test-motor?variant=123") == "test-motor"

    def test_url_with_fragment(self):
        assert plugin.extract_handle("https://www.t-hobby.com/products/test-motor#specs") == "test-motor"

    def test_url_without_products_path_returns_none(self):
        assert plugin.extract_handle("https://www.t-hobby.com/collections/motors") is None


class TestTHobbyParseProduct:
    def test_basic_fields(self):
        result = plugin.parse_product(FAKE_RAW)
        assert result["handle"] == "tmotor-its-2306-5-powerful-freestyle-motor-1750kv"
        assert result["title"] == "T-Motor ITS 2306.5 Powerful Freestyle Motor"
        assert result["image_url"] == "https://img.fantaskycdn.com/test-motor.jpeg"
        assert result["product_url"] == "https://www.t-hobby.com/products/tmotor-its-2306-5-powerful-freestyle-motor-1750kv"
        assert len(result["variants"]) == 1

    def test_variant_fields(self):
        v = plugin.parse_product(FAKE_RAW)["variants"][0]
        assert v["external_variant_id"] == "35ddd7e9-2b6a-419f-a056-e13042272b13"
        assert v["name"] == "1750KV"
        assert v["sku"] == "AHD01010017"
        assert v["price"] == 26.9
        assert v["compare_at_price"] == 29.9
        assert v["in_stock"] is True

    def test_out_of_stock_variant(self):
        raw = {**FAKE_RAW, "variants": [{**FAKE_RAW["variants"][0], "available": False}]}
        v = plugin.parse_product(raw)["variants"][0]
        assert v["in_stock"] is False

    def test_no_compare_at_price(self):
        raw = {**FAKE_RAW, "variants": [{**FAKE_RAW["variants"][0], "compare_at_price": None}]}
        v = plugin.parse_product(raw)["variants"][0]
        assert v["compare_at_price"] is None

    def test_no_image(self):
        raw = {**FAKE_RAW, "image": None}
        result = plugin.parse_product(raw)
        assert result["image_url"] is None

    def test_multiple_variants(self):
        raw = {
            **FAKE_RAW,
            "variants": [
                {**FAKE_RAW["variants"][0], "id": "v1", "title": "1750KV"},
                {**FAKE_RAW["variants"][0], "id": "v2", "title": "2020KV"},
            ],
        }
        result = plugin.parse_product(raw)
        assert len(result["variants"]) == 2
        assert result["variants"][1]["name"] == "2020KV"


class TestTHobbyFetchProduct:
    def _mock_ok_response(self, html: str):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.text = html
        return resp

    def _mock_error_response(self, status_code: int):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status_code
        return resp

    def test_successful_200_returns_product_dict(self):
        html = _make_html(FAKE_RAW)
        mock_resp = self._mock_ok_response(html)
        with patch("httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value.get.return_value = mock_resp
            result = plugin.fetch_product("test-motor")
        assert result is not None
        assert result["handle"] == "tmotor-its-2306-5-powerful-freestyle-motor-1750kv"

    def test_non_200_returns_none(self):
        mock_resp = self._mock_error_response(403)
        with patch("httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value.get.return_value = mock_resp
            result = plugin.fetch_product("nonexistent")
        assert result is None

    def test_network_exception_returns_none(self):
        with patch("httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value.get.side_effect = httpx.ConnectError("timeout")
            result = plugin.fetch_product("test-motor")
        assert result is None

    def test_missing_product_json_returns_none(self):
        mock_resp = self._mock_ok_response("<html><body>No product data</body></html>")
        with patch("httpx.Client") as mock_cls:
            mock_cls.return_value.__enter__.return_value.get.return_value = mock_resp
            result = plugin.fetch_product("test-motor")
        assert result is None

    def test_requests_correct_url(self):
        html = _make_html(FAKE_RAW)
        mock_resp = self._mock_ok_response(html)
        with patch("httpx.Client") as mock_cls:
            mock_get = mock_cls.return_value.__enter__.return_value.get
            mock_get.return_value = mock_resp
            plugin.fetch_product("test-motor")
        called_url = mock_get.call_args[0][0]
        assert called_url == "https://www.t-hobby.com/products/test-motor"
