"""Loopback API origins are restricted to the app and supported dev UI."""


def test_supported_development_origin_can_read_api(client):
    response = client.get(
        "/api/jobs", headers={"Origin": "http://localhost:5173"}
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-credentials" not in response.headers


def test_unrelated_origin_is_rejected_even_for_simple_get(client):
    response = client.get(
        "/api/jobs", headers={"Origin": "https://evil.example.com"}
    )

    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


def test_cross_site_fetch_metadata_is_rejected(client):
    response = client.get(
        "/api/jobs", headers={"Sec-Fetch-Site": "cross-site"}
    )

    assert response.status_code == 403


def test_rebound_dns_host_is_rejected(client_factory):
    # Same-origin GET after DNS rebinding: no Origin, only a foreign Host.
    client = client_factory(base_url="http://evil.example:8000")

    response = client.get(
        "/api/candidates/me", headers={"Sec-Fetch-Site": "same-origin"}
    )

    assert response.status_code == 400


def test_same_origin_loopback_webview_is_allowed(client_factory):
    origin = "http://127.0.0.1:43210"
    client = client_factory(base_url=origin)

    response = client.patch(
        "/api/candidates/me",
        json={"headline": "Local profile"},
        headers={"Origin": origin, "Sec-Fetch-Site": "same-origin"},
    )

    assert response.status_code == 200


def test_explicit_preflight_is_limited_to_supported_methods_and_headers(client):
    response = client.options(
        "/api/candidates/me",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "PATCH" in response.headers["access-control-allow-methods"]
    assert "*" not in response.headers["access-control-allow-headers"]
