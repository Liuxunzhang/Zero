"""SymbolService index caching, filtering and download guards (no network)."""

import threading
import time

import pytest

from web.backend.services import symbol_service as svc_mod
from web.backend.services.symbol_service import SymbolService, _parse_repo


@pytest.fixture
def service(tmp_path, monkeypatch):
    """A SymbolService whose disk index and symbol root live under tmp_path."""
    monkeypatch.setattr(svc_mod, "_DISK_INDEX_DIR", tmp_path / "remote_index")
    monkeypatch.setattr(svc_mod, "_resolve_symbol_root", lambda: tmp_path / "symbols")
    return SymbolService()


def _items(*paths):
    return [SymbolService._make_item(p, 10, "owner/repo") for p in paths]


def test_concurrent_refresh_makes_one_network_call(service, monkeypatch):
    ref = _parse_repo("Abyss-W4tcher/volatility3-symbols")
    calls = []

    def fake_build(_ref):
        calls.append(1)
        time.sleep(0.05)  # widen the window every thread could race through
        return _items("Ubuntu/5.15.0-generic.json"), "master"

    monkeypatch.setattr(service, "_build_remote_index", fake_build)

    threads = [
        threading.Thread(target=lambda: service._ensure_remote_index(ref))
        for _ in range(6)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1
    assert len(service._state(ref)["remote_index"]) == 1


def test_search_keys_track_index_and_filter(service, monkeypatch):
    monkeypatch.setattr(
        service,
        "_build_remote_index",
        lambda _ref: (
            _items(
                "Ubuntu/5.15.0-91-generic.json.xz",
                "Debian/6.1.0-debian.json",
                "Windows/ntkrnlmp.pdb.json",
            ),
            "master",
        ),
    )

    out = service.list_remote_symbols(query="UBUNTU")
    assert out["total"] == 1
    assert out["items"][0]["path"].startswith("Ubuntu/")

    # search_keys must stay aligned with remote_index or filtering silently skews.
    state = service._state(_parse_repo("Abyss-W4tcher/volatility3-symbols"))
    assert len(state["search_keys"]) == len(state["remote_index"])

    by_os = service.list_remote_symbols(os_family="windows")
    assert by_os["total"] == 1
    assert by_os["items"][0]["os"] == "windows"


def test_search_keys_are_not_persisted_to_disk(service, monkeypatch):
    monkeypatch.setattr(
        service,
        "_build_remote_index",
        lambda _ref: (_items("Ubuntu/a.json"), "master"),
    )
    service.list_remote_symbols()

    index_files = list((svc_mod._DISK_INDEX_DIR).glob("*.json"))
    assert index_files
    import json

    payload = json.loads(index_files[0].read_text(encoding="utf-8"))
    assert payload["items"] == [
        {"path": "Ubuntu/a.json", "name": "a.json", "size": 10, "os": "linux", "repo": "owner/repo"}
    ]


def test_download_rejects_paths_outside_index_and_root(service, monkeypatch):
    monkeypatch.setattr(
        service,
        "_build_remote_index",
        lambda _ref: (_items("Ubuntu/ok.json"), "master"),
    )
    fetched = []

    def fake_fetch(_ref, _branch, rel, target):
        fetched.append(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"{}")
        return {"bucket": "downloaded", "path": rel, "size": 2, "repo": "owner/repo"}

    monkeypatch.setattr(service, "_fetch_one_symbol", fake_fetch)

    out = service.download_symbols(
        ["Ubuntu/ok.json", "../../etc/passwd", "Ubuntu/not-in-index.json"]
    )

    assert fetched == ["Ubuntu/ok.json"]
    assert [d["path"] for d in out["downloaded"]] == ["Ubuntu/ok.json"]
    reasons = {f["reason"] for f in out["failed"]}
    assert reasons == {"not_found_in_remote_index"}


def test_download_skips_existing_files(service, monkeypatch):
    monkeypatch.setattr(
        service,
        "_build_remote_index",
        lambda _ref: (_items("Ubuntu/ok.json"), "master"),
    )
    root = svc_mod._resolve_symbol_root()
    (root / "Ubuntu").mkdir(parents=True, exist_ok=True)
    (root / "Ubuntu" / "ok.json").write_bytes(b"{}")

    def boom(*_args):
        raise AssertionError("must not download an existing file")

    monkeypatch.setattr(service, "_fetch_one_symbol", boom)

    out = service.download_symbols(["Ubuntu/ok.json"])
    assert [s["path"] for s in out["skipped"]] == ["Ubuntu/ok.json"]
