"""FastAPI route smoke tests (no real Volatility runs).

These exercise the request-validation and middleware layers only — the vol3
engine is lazily initialized, and none of these endpoints trigger a plugin run.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from web.backend import main

    return TestClient(main.app)


def test_health_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_load_image_rejects_bad_extension(client):
    r = client.post("/api/image/load", json={"path": "/tmp/not-an-image.txt"})
    assert r.status_code == 400
    assert "extension" in r.json()["detail"].lower()


def test_load_image_missing_file_404(client, tmp_path):
    img = tmp_path / "ghost.raw"  # valid extension, does not exist
    r = client.post("/api/image/load", json={"path": str(img)})
    assert r.status_code == 404


def test_export_rejects_bad_format(client):
    r = client.post("/api/export", json={"format": "pdf"})
    assert r.status_code == 400
    assert "format" in r.json()["detail"].lower()


def test_plugins_rejects_bad_os_family(client):
    r = client.get("/api/plugins/solaris")
    assert r.status_code == 400


def test_results_page_validation(client):
    # page must be >= 1 (Query ge=1) -> 422 from FastAPI validation.
    r = client.get("/api/results", params={"page": 0})
    assert r.status_code == 422


# ── Token middleware ────────────────────────────────────────────────


@pytest.fixture
def token_client(monkeypatch):
    """A client whose backend requires API token 'secret'."""
    from zero import config

    monkeypatch.setattr(config, "API_TOKEN", "secret")
    from web.backend import main

    # Middleware reads config.API_TOKEN per request, so no app rebuild needed.
    return TestClient(main.app)


def test_token_required_when_configured(token_client):
    assert token_client.get("/api/engines").status_code == 401


def test_token_health_is_public(token_client):
    assert token_client.get("/api/health").status_code == 200


def test_token_accepted_via_header(token_client):
    r = token_client.get("/api/engines", headers={"X-API-Token": "secret"})
    assert r.status_code == 200


def test_token_accepted_via_bearer(token_client):
    r = token_client.get("/api/engines", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200


def test_token_wrong_value_rejected(token_client):
    r = token_client.get("/api/engines", headers={"X-API-Token": "nope"})
    assert r.status_code == 401
