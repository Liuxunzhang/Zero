"""SymbolService index caching, filtering and download guards (no network)."""

import threading
import time

import pytest

from web.backend.services import symbol_service as svc_mod
from web.backend.services.symbol_service import SymbolService, _parse_repo
from zero.core.kernel_detector import LinuxKernelInfo


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

    def fake_fetch(_ref, _branch, rel, target, **_kwargs):
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

    def boom(*_args, **_kwargs):
        raise AssertionError("must not download an existing file")

    monkeypatch.setattr(service, "_fetch_one_symbol", boom)

    out = service.download_symbols(["Ubuntu/ok.json"])
    assert [s["path"] for s in out["skipped"]] == ["Ubuntu/ok.json"]


def test_download_can_use_fixed_gh_proxy(service, monkeypatch):
    path = "Ubuntu/ok.json"
    monkeypatch.setattr(
        service,
        "_build_remote_index",
        lambda _ref: (_items(path), "master"),
    )
    calls = []

    def fake_fetch(
        _ref,
        _branch,
        rel,
        target,
        *,
        use_gh_proxy=False,
        progress_callback=None,
    ):
        calls.append((rel, use_gh_proxy))
        if progress_callback:
            progress_callback(1, 2)
            progress_callback(2, 2)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"{}")
        return {"bucket": "downloaded", "path": rel, "size": 2, "repo": "owner/repo"}

    monkeypatch.setattr(service, "_fetch_one_symbol", fake_fetch)

    out = service.download_symbols([path], use_gh_proxy=True)

    assert calls == [(path, True)]
    assert out["use_gh_proxy"] is True
    ref = _parse_repo("Abyss-W4tcher/volatility3-symbols")
    assert service._symbol_download_url(ref, "master", path, use_gh_proxy=True) == (
        "https://gh-proxy.com/https://raw.githubusercontent.com/"
        "Abyss-W4tcher/volatility3-symbols/master/Ubuntu/ok.json"
    )


def test_auto_download_uses_exact_detected_release(service, monkeypatch, tmp_path):
    image = tmp_path / "memory.raw"
    image.write_bytes(
        b"prefix\x00Linux version 5.15.0-91-generic "
        b"(buildd@lcy02-amd64-001) (Ubuntu 11.4.0) #101-Ubuntu SMP\x00"
    )
    symbol_path = (
        "Ubuntu/amd64/5.15.0/91/generic/"
        "Ubuntu_5.15.0-91-generic_5.15.0-91.101_amd64.json.xz"
    )
    monkeypatch.setattr(
        service,
        "_build_remote_index",
        lambda _ref: (_items(symbol_path), "master"),
    )

    fetched = []

    def fake_fetch(_ref, _branch, rel, target, **kwargs):
        fetched.append(rel)
        progress_callback = kwargs.get("progress_callback")
        if progress_callback:
            progress_callback(1, 2)
            progress_callback(2, 2)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"{}")
        return {"bucket": "downloaded", "path": rel, "size": 2, "repo": "owner/repo"}

    monkeypatch.setattr(service, "_fetch_one_symbol", fake_fetch)

    progress_events = []
    out = service.auto_download_for_image(
        str(image),
        progress_callback=progress_events.append,
    )

    assert out["status"] == "available"
    assert out["kernel"]["release"] == "5.15.0-91-generic"
    assert out["kernel"]["distro"] == "ubuntu"
    assert out["candidates"] == [symbol_path]
    assert fetched == []
    assert [event["stage"] for event in progress_events[:2]] == ["detecting", "matching"]

    out = service.auto_download_for_image(
        str(image),
        download=True,
        progress_callback=progress_events.append,
    )
    assert out["status"] == "downloaded"
    assert fetched == [symbol_path]
    download_events = [event for event in progress_events if event["stage"] == "downloading"]
    assert download_events
    assert download_events[-1]["percent"] == 100.0


def test_auto_check_returns_local_match_without_remote_index(service, monkeypatch, tmp_path):
    image = tmp_path / "memory.raw"
    image.write_bytes(
        b"Linux version 6.12.96+deb13-amd64 "
        b"(debian-kernel@lists.debian.org) #1 SMP Debian\x00"
    )
    root = svc_mod._resolve_symbol_root()
    root.mkdir(parents=True)
    (root / "debian-6.12.96+deb13-amd64.json.xz").write_bytes(b"symbol")

    def boom(*_args, **_kwargs):
        raise AssertionError("local exact match must be checked before GitHub")

    monkeypatch.setattr(service, "_ensure_remote_index", boom)

    out = service.auto_download_for_image(str(image))

    assert out["status"] == "present"
    assert out["kernel"]["release"] == "6.12.96+deb13-amd64"
    assert out["local_matches"][0]["path"] == "debian-6.12.96+deb13-amd64.json.xz"


def test_auto_download_refuses_too_many_equally_good_candidates(service, monkeypatch, tmp_path):
    image = tmp_path / "memory.raw"
    image.write_bytes(
        b"Linux version 6.1.0-23-amd64 "
        b"(debian-kernel@lists.debian.org) #1 SMP Debian\x00"
    )
    paths = [
        f"Debian/amd64/6.1.0/23/variant-{i}/Debian_6.1.0-23-amd64_{i}_amd64.json.xz"
        for i in range(5)
    ]
    monkeypatch.setattr(
        service,
        "_build_remote_index",
        lambda _ref: (_items(*paths), "master"),
    )
    monkeypatch.setattr(svc_mod.config, "AUTO_SYMBOL_DOWNLOAD_MAX_CANDIDATES", 4)

    def boom(*_args, **_kwargs):
        raise AssertionError("ambiguous candidates must not be downloaded")

    monkeypatch.setattr(service, "_fetch_one_symbol", boom)

    out = service.auto_download_for_image(str(image))

    assert out["status"] == "ambiguous"
    assert out["candidate_count"] == 5


def test_auto_candidate_match_does_not_accept_release_prefix(service):
    kernel = LinuxKernelInfo(
        banner="Linux version 5.15.0-101-generic (Ubuntu) #1",
        release="5.15.0-101-generic",
        offset=0,
        distro="ubuntu",
        architecture="x86_64",
    )
    exact = "Ubuntu/amd64/Ubuntu_5.15.0-101-generic_amd64.json.xz"
    longer = "Ubuntu/amd64/Ubuntu_5.15.0-1011-generic_amd64.json.xz"

    ranked = service._rank_linux_candidates(_items(longer, exact), kernel)

    assert [item["path"] for _score, item in ranked] == [exact]
