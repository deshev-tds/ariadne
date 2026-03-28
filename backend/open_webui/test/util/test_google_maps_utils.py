from types import SimpleNamespace

from open_webui.utils.google_maps import (
    build_google_maps_search_url,
    normalize_language_code,
    normalize_region_code,
    probe_google_maps_integration,
    resolve_runtime_language_code,
)


def test_normalize_language_code_supports_bcp47_shape():
    assert normalize_language_code("en-us") == "en-US"
    assert normalize_language_code("sw") == "sw"
    assert normalize_language_code("ru_RU") == "ru-RU"


def test_normalize_region_code_uppercases_two_letter_codes():
    assert normalize_region_code("it") == "IT"
    assert normalize_region_code("BG") == "BG"
    assert normalize_region_code("italy") is None


def test_resolve_runtime_language_code_prefers_explicit_override():
    request = SimpleNamespace(headers={"accept-language": "es-ES,es;q=0.9"})
    assert resolve_runtime_language_code(request, "ru", "en") == "ru"


def test_resolve_runtime_language_code_falls_back_to_request_header_then_default():
    request = SimpleNamespace(headers={"accept-language": "es-ES,es;q=0.9"})
    assert resolve_runtime_language_code(request, None, "en") == "es-ES"
    assert resolve_runtime_language_code(SimpleNamespace(headers={}), None, "en") == "en"


def test_build_google_maps_search_url_includes_query_place_id():
    url = build_google_maps_search_url("Enoteca Pinchiorri, Florence", "abc123")
    assert "query_place_id=abc123" in url
    assert "api=1" in url


class _DummyResponse:
    def __init__(self, ok, status_code, body):
        self.ok = ok
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


def test_admin_test_path_passes_through_raw_upstream_error(monkeypatch):
    def fake_search(**kwargs):
        return _DummyResponse(
            False,
            403,
            {"error": {"code": 403, "message": "API key not valid for this IP"}},
        )

    monkeypatch.setattr(
        "open_webui.utils.google_maps._search_text_place_ids_raw",
        fake_search,
    )

    config = SimpleNamespace(
        ENABLE_GOOGLE_MAPS=False,
        GOOGLE_MAPS_API_KEY="test-key",
        GOOGLE_MAPS_BASE_URL="https://places.googleapis.com",
        GOOGLE_MAPS_TIMEOUT_SECONDS=10,
        GOOGLE_MAPS_DEFAULT_LANGUAGE_CODE="",
        GOOGLE_MAPS_DEFAULT_REGION_CODE="",
        GOOGLE_MAPS_MAX_CANDIDATES=5,
    )
    request = SimpleNamespace(headers={})

    result = probe_google_maps_integration(
        config=config,
        request=request,
        place_name="Enoteca Pinchiorri",
        location_context="Florence, Italy",
    )

    assert result["ok"] is False
    assert result["enabled"] is False
    assert result["search"]["upstream_status"] == 403
    assert result["search"]["body"]["error"]["message"] == "API key not valid for this IP"
