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


def test_load_image_returns_before_auto_symbol_download(client, tmp_path, monkeypatch):
    from web.backend.api import routes

    image = tmp_path / "memory.raw"
    image.write_bytes(b"image")

    class FakeEngineService:
        def load_image(self, path, engine_id="vol3"):
            assert path == str(image)
            assert engine_id == "vol3"
            return True

    monkeypatch.setattr(routes, "get_service", lambda: FakeEngineService())

    r = client.post("/api/image/load", json={"path": str(image)})

    assert r.status_code == 200
    assert r.json() == {"ok": True, "path": str(image), "engine": "vol3"}
    assert "symbol_download" not in r.json()


def test_auto_symbol_download_streams_progress_after_load(client, tmp_path, monkeypatch):
    import json

    from web.backend.api import routes

    image = tmp_path / "memory.raw"
    image.write_bytes(b"image")

    class FakeSymbolService:
        def auto_download_for_image(self, path, *, progress_callback=None):
            assert path == str(image.resolve())
            progress_callback(
                {
                    "stage": "downloading",
                    "percent": 50.0,
                    "completed_files": 0,
                    "total_files": 1,
                }
            )
            return {
                "enabled": True,
                "status": "downloaded",
                "downloaded": [{"path": "Debian/kernel.json.xz"}],
            }

    monkeypatch.setattr(routes, "get_symbol_service", lambda: FakeSymbolService())

    r = client.post("/api/image/symbols/auto", json={"path": str(image)})

    assert r.status_code == 200
    events = [json.loads(line) for line in r.text.splitlines()]
    assert events[0]["type"] == "progress"
    assert events[0]["data"]["percent"] == 50.0
    assert events[1]["type"] == "result"
    assert events[1]["data"]["status"] == "downloaded"


def test_symbol_download_forwards_gh_proxy_choice(client, monkeypatch):
    from web.backend.api import symbol_routes

    calls = []

    class FakeSymbolService:
        def download_symbols(self, paths, repo="", *, use_gh_proxy=False):
            calls.append((paths, repo, use_gh_proxy))
            return {
                "downloaded": [],
                "skipped": [],
                "failed": [],
                "root": "/tmp/symbols",
                "repo": repo,
                "use_gh_proxy": use_gh_proxy,
            }

    monkeypatch.setattr(symbol_routes, "get_symbol_service", lambda: FakeSymbolService())

    r = client.post(
        "/api/symbols/download",
        json={"paths": ["Ubuntu/test.json"], "repo": "owner/repo", "use_gh_proxy": True},
    )

    assert r.status_code == 200
    assert calls == [(["Ubuntu/test.json"], "owner/repo", True)]
    assert r.json()["use_gh_proxy"] is True


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
