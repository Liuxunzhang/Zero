"""Tests for ``zero.core.result_cache.DiskResultCache``.

Focus on the kwargs-aware cache key: different plugin parameters (pid/offset/
key) must produce distinct on-disk files, while ``delete`` purges every
variant of a plugin.  No network, no vol3 — pure filesystem tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zero.core.result_cache import DiskResultCache


@pytest.fixture
def cache(tmp_path: Path) -> DiskResultCache:
    return DiskResultCache(tmp_path / "results", enabled=True)


def _sample_result():
    return (["PID", "Name"], [(4, "a"), (7, "b")])


# ── _kwargs_signature ────────────────────────────────────────────────

def test_kwargs_signature_empty_for_none_or_empty(cache):
    assert cache._kwargs_signature(None) == ""
    assert cache._kwargs_signature({}) == ""


def test_kwargs_signature_stable(cache):
    """The same kwargs dict must hash identically across calls/orderings."""
    sig1 = cache._kwargs_signature({"pid": 4376, "offset": 16})
    sig2 = cache._kwargs_signature({"offset": 16, "pid": 4376})
    assert sig1 == sig2
    assert len(sig1) == 12


def test_kwargs_signature_distinct_for_different_values(cache):
    assert cache._kwargs_signature({"pid": 4376}) != cache._kwargs_signature({"pid": 100})


def test_kwargs_signature_distinct_for_different_keys(cache):
    # Same value, different key → different signature.
    assert cache._kwargs_signature({"pid": 4376}) != cache._kwargs_signature({"offset": 4376})


def test_kwargs_signature_handles_types(cache):
    """int / str / bool values all hash without raising."""
    for kwargs in [
        {"pid": 0},
        {"key": "HKLM\\Software"},
        {"physical": True},
        {"ignore-case": False, "regex": "svchost"},
    ]:
        sig = cache._kwargs_signature(kwargs)
        assert isinstance(sig, str) and len(sig) == 12


# ── file path encoding ───────────────────────────────────────────────

def test_file_path_no_kwargs_suffix(cache):
    path = cache._result_file_path("/img/memory.raw", "windows.pslist.PsList", None)
    assert path.name == "windows.pslist.PsList.csv"


def test_file_path_with_kwargs_suffix(cache):
    path = cache._result_file_path("/img/memory.raw", "windows.handles.Handles", {"pid": 4376})
    assert path.name.startswith("windows.handles.Handles__")
    assert path.name.endswith(".csv")
    assert len(path.name) > len("windows.handles.Handles__xxxxxxxxxxxx.csv") - 1


# ── load / save with kwargs ──────────────────────────────────────────

def test_save_load_roundtrip_with_same_kwargs(cache):
    cols, rows = _sample_result()
    cache.save("/img/memory.raw", "windows.handles.Handles", cols, rows, {"pid": 4376})
    loaded = cache.load("/img/memory.raw", "windows.handles.Handles", {"pid": 4376})
    assert loaded is not None
    assert loaded[0] == cols
    # CSV is type-less: ints round-trip as strings.  Compare by string value.
    assert loaded[1] == [tuple(str(c) for c in r) for r in rows]


def test_load_with_different_kwargs_misses(cache):
    cols, rows = _sample_result()
    cache.save("/img/memory.raw", "windows.handles.Handles", cols, rows, {"pid": 4376})
    assert cache.load("/img/memory.raw", "windows.handles.Handles", {"pid": 100}) is None


def test_load_without_kwargs_misses_kwargs_variant(cache):
    """A kwargs-aware save must NOT be loadable via the bare (no-kwargs) path."""
    cols, rows = _sample_result()
    cache.save("/img/memory.raw", "windows.handles.Handles", cols, rows, {"pid": 4376})
    assert cache.load("/img/memory.raw", "windows.handles.Handles", None) is None


def test_save_without_kwargs_keeps_plain_filename(cache):
    """Backwards-compat: parameter-less plugins keep their plain CSV name."""
    cols, rows = _sample_result()
    cache.save("/img/memory.raw", "windows.pslist.PsList", cols, rows, None)
    # Loading with the same (empty) kwargs hits.
    assert cache.load("/img/memory.raw", "windows.pslist.PsList", None) is not None


# ── delete / delete_all ──────────────────────────────────────────────

def test_delete_removes_all_kwargs_variants(cache):
    cols, rows = _sample_result()
    cache.save("/img/m.raw", "windows.handles.Handles", cols, rows, {"pid": 4376})
    cache.save("/img/m.raw", "windows.handles.Handles", cols, rows, {"pid": 100})
    cache.save("/img/m.raw", "windows.handles.Handles", cols, rows, {"pid": 7})

    removed = cache.delete("/img/m.raw", "windows.handles.Handles")
    assert removed == 3
    # All variants gone.
    assert cache.load("/img/m.raw", "windows.handles.Handles", {"pid": 4376}) is None
    assert cache.load("/img/m.raw", "windows.handles.Handles", {"pid": 100}) is None


def test_delete_only_targets_named_plugin(cache):
    """delete must not touch other plugins' files."""
    cache.save("/img/m.raw", "windows.pslist.PsList", *_sample_result(), kwargs=None)
    cache.save("/img/m.raw", "windows.handles.Handles", *_sample_result(), kwargs={"pid": 1})

    cache.delete("/img/m.raw", "windows.handles.Handles")

    assert cache.load("/img/m.raw", "windows.pslist.PsList", None) is not None
    assert cache.load("/img/m.raw", "windows.handles.Handles", {"pid": 1}) is None


def test_delete_all_clears_entire_image(cache):
    cache.save("/img/m.raw", "windows.pslist.PsList", *_sample_result(), kwargs=None)
    cache.save("/img/m.raw", "windows.handles.Handles", *_sample_result(), kwargs={"pid": 1})

    removed = cache.delete_all("/img/m.raw")
    assert removed >= 2
    assert cache.load("/img/m.raw", "windows.pslist.PsList", None) is None
    assert cache.load("/img/m.raw", "windows.handles.Handles", {"pid": 1}) is None


def test_delete_returns_zero_for_unknown_image(cache):
    assert cache.delete("/nonexistent/m.raw", "windows.handles.Handles") == 0
    assert cache.delete_all("/nonexistent/m.raw") == 0


def test_delete_includes_plain_variant(cache):
    """delete must also remove the no-kwargs ``{plugin}.csv`` variant."""
    cache.save("/img/m.raw", "windows.pslist.PsList", *_sample_result(), kwargs=None)
    removed = cache.delete("/img/m.raw", "windows.pslist.PsList")
    assert removed == 1
