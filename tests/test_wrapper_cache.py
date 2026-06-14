"""Tests for ``VolatilityWrapper`` cache-key and clear behaviour.

These exercise the kwargs-aware cache key (so different pid/offset/key values
don't collide), the ``use_cache=False`` write-skip, and ``clear_cache``
prefix-clear semantics.  No real vol3 runs — we construct a wrapper via
``__new__`` (bypassing ``_init_volatility``) and test the cache machinery
directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zero.core.wrapper import VolatilityWrapper


@pytest.fixture
def wrapper(tmp_path: Path) -> VolatilityWrapper:
    """A wrapper instance with a tmp results dir, no vol3 initialisation."""
    import threading
    from zero.core.result_cache import DiskResultCache

    w = VolatilityWrapper.__new__(VolatilityWrapper)
    w.image_path = "/img/memory.raw"
    w.plugin_list = [
        "windows.pslist.PsList",
        "windows.handles.Handles",
        "windows.memmap.Memmap",
    ]
    w._display_to_full_plugin_name = {}
    w._current_plugin_family = "windows"
    w._cache = {}
    w._cache_stats = {
        "memory_hits": 0,
        "disk_hits": 0,
        "misses": 0,
        "disk_writes": 0,
        "clear_operations": 0,
    }
    # State primitives that the cache/clear paths read through their locks.
    w._state_lock = threading.Lock()
    w._plugin_running = False
    w._cancel_requested = threading.Event()
    w._worker_process = None
    w._disk_cache = DiskResultCache(tmp_path / "results", enabled=True, fmt="csv")
    return w


# ── _cache_key ───────────────────────────────────────────────────────

def test_cache_key_includes_pid(wrapper):
    key_4376 = wrapper._cache_key("windows.handles.Handles", {"pid": 4376})
    key_100 = wrapper._cache_key("windows.handles.Handles", {"pid": 100})
    assert key_4376 != key_100, "different pid must produce different cache keys"


def test_cache_key_plugin_in_first_slot(wrapper):
    """The resolved plugin name is key[0] so clear_cache can prefix-match."""
    key = wrapper._cache_key("windows.handles.Handles", {"pid": 4376})
    assert key[0] == "windows.handles.Handles"


def test_cache_key_same_kwargs_stable(wrapper):
    k1 = wrapper._cache_key("windows.handles.Handles", {"pid": 4376})
    k2 = wrapper._cache_key("windows.handles.Handles", {"pid": 4376})
    assert k1 == k2


def test_cache_key_excludes_dump_dir(wrapper):
    """dump_dir (timestamped per-run) must NOT fragment the cache key."""
    key_a = wrapper._cache_key("windows.memmap.Memmap", {"pid": 1, "dump_dir": "/tmp/a_1700000000"})
    key_b = wrapper._cache_key("windows.memmap.Memmap", {"pid": 1, "dump_dir": "/tmp/b_1700000001"})
    assert key_a == key_b, "dump_dir must be excluded from the cache key"


def test_cache_key_excludes_dump_flag(wrapper):
    key_a = wrapper._cache_key("windows.memmap.Memmap", {"pid": 1, "dump": True})
    key_b = wrapper._cache_key("windows.memmap.Memmap", {"pid": 1, "dump": False})
    assert key_a == key_b


def test_cache_key_no_kwargs(wrapper):
    key = wrapper._cache_key("windows.pslist.PsList", {})
    assert key == ("windows.pslist.PsList", ())


# ── clear_cache prefix clear (in-memory) ─────────────────────────────

def test_clear_cache_removes_all_kwargs_variants(wrapper):
    """clear_cache(plugin) must wipe every pid/offset variant of that plugin."""
    k1 = wrapper._cache_key("windows.handles.Handles", {"pid": 4376})
    k2 = wrapper._cache_key("windows.handles.Handles", {"pid": 100})
    k3 = wrapper._cache_key("windows.handles.Handles", {"pid": 7})
    other = wrapper._cache_key("windows.pslist.PsList", {})
    wrapper._cache = {k1: (["a"], []), k2: (["b"], []), k3: (["c"], []), other: (["d"], [])}

    wrapper.clear_cache("handles.Handles")

    assert k1 not in wrapper._cache
    assert k2 not in wrapper._cache
    assert k3 not in wrapper._cache
    # Other plugin untouched.
    assert other in wrapper._cache


def test_clear_cache_all_wipes_everything(wrapper):
    k1 = wrapper._cache_key("windows.handles.Handles", {"pid": 4376})
    k2 = wrapper._cache_key("windows.pslist.PsList", {})
    wrapper._cache = {k1: (["a"], []), k2: (["b"], [])}

    wrapper.clear_cache(None)

    assert wrapper._cache == {}


def test_clear_cache_increments_stat(wrapper):
    wrapper.clear_cache("handles.Handles")
    assert wrapper._cache_stats["clear_operations"] == 1


# ── run_plugin cache write/skip behaviour ────────────────────────────
#
# We can't easily exercise the full run_plugin path without vol3, but we can
# verify the cache-write/skip decision by inspecting _cache after a mocked
# run.  We patch the subprocess worker to a stub and assert use_cache gates
# the write.

def test_use_cache_false_skips_cache_write(wrapper, monkeypatch):
    """use_cache=False must not populate the in-memory or disk cache."""
    from zero.core import wrapper as wrapper_mod

    # Stub the heavy worker path so run_plugin returns deterministic rows.
    def _stub_run(self, plugin_name, progress_callback, plugin_kwargs=None):
        return (["PID", "Name"], [(1, "stub")])

    # hard_interrupt_mode is required to reach the worker path; force it on.
    monkeypatch.setattr(wrapper_mod.VolatilityWrapper, "hard_interrupt_mode", True, raising=False)
    monkeypatch.setattr(
        wrapper_mod.VolatilityWrapper,
        "_should_use_subprocess_worker",
        lambda self: True,
        raising=False,
    )
    monkeypatch.setattr(wrapper_mod.VolatilityWrapper, "_run_plugin_via_subprocess", _stub_run)
    monkeypatch.setattr(wrapper_mod, "VOLATILITY_AVAILABLE", True, raising=False)
    monkeypatch.setattr(wrapper_mod.VolatilityWrapper, "symbol_dirs", [], raising=False)

    cols, rows = wrapper.run_plugin("pslist.PsList", use_cache=False, pid=1)

    assert cols == ["PID", "Name"]
    # Nothing should have been written to either cache layer.
    assert wrapper._cache == {}


def test_use_cache_true_populates_cache(wrapper, monkeypatch):
    """use_cache=True (default) must populate the cache after a successful run."""
    from zero.core import wrapper as wrapper_mod

    def _stub_run(self, plugin_name, progress_callback, plugin_kwargs=None):
        return (["PID", "Name"], [(1, "stub")])

    monkeypatch.setattr(wrapper_mod.VolatilityWrapper, "hard_interrupt_mode", True, raising=False)
    monkeypatch.setattr(
        wrapper_mod.VolatilityWrapper,
        "_should_use_subprocess_worker",
        lambda self: True,
        raising=False,
    )
    monkeypatch.setattr(wrapper_mod.VolatilityWrapper, "_run_plugin_via_subprocess", _stub_run)
    monkeypatch.setattr(wrapper_mod, "VOLATILITY_AVAILABLE", True, raising=False)
    monkeypatch.setattr(wrapper_mod.VolatilityWrapper, "symbol_dirs", [], raising=False)

    wrapper.run_plugin("pslist.PsList", pid=1)

    expected_key = wrapper._cache_key("windows.pslist.PsList", {"pid": 1})
    assert expected_key in wrapper._cache
    assert wrapper._cache[expected_key] == (["PID", "Name"], [(1, "stub")])


def test_cache_hit_skips_execution(wrapper, monkeypatch):
    """Once cached, a second run_plugin call must NOT invoke the worker."""
    from zero.core import wrapper as wrapper_mod

    calls = []

    def _stub_run(self, plugin_name, progress_callback, plugin_kwargs=None):
        calls.append(plugin_name)
        return (["PID"], [(1,)])

    monkeypatch.setattr(wrapper_mod.VolatilityWrapper, "hard_interrupt_mode", True, raising=False)
    monkeypatch.setattr(
        wrapper_mod.VolatilityWrapper,
        "_should_use_subprocess_worker",
        lambda self: True,
        raising=False,
    )
    monkeypatch.setattr(wrapper_mod.VolatilityWrapper, "_run_plugin_via_subprocess", _stub_run)
    monkeypatch.setattr(wrapper_mod, "VOLATILITY_AVAILABLE", True, raising=False)
    monkeypatch.setattr(wrapper_mod.VolatilityWrapper, "symbol_dirs", [], raising=False)

    wrapper.run_plugin("pslist.PsList", pid=1)
    wrapper.run_plugin("pslist.PsList", pid=1)  # should hit cache

    assert len(calls) == 1, "second run must be served from cache"


def test_different_pid_re_executes(wrapper, monkeypatch):
    """Different pid must NOT hit the cache of another pid."""
    from zero.core import wrapper as wrapper_mod

    calls = []

    def _stub_run(self, plugin_name, progress_callback, plugin_kwargs=None):
        pid = (plugin_kwargs or {}).get("pid")
        calls.append(pid)
        return ([f"col_{pid}"], [(pid,)])

    monkeypatch.setattr(wrapper_mod.VolatilityWrapper, "hard_interrupt_mode", True, raising=False)
    monkeypatch.setattr(
        wrapper_mod.VolatilityWrapper,
        "_should_use_subprocess_worker",
        lambda self: True,
        raising=False,
    )
    monkeypatch.setattr(wrapper_mod.VolatilityWrapper, "_run_plugin_via_subprocess", _stub_run)
    monkeypatch.setattr(wrapper_mod, "VOLATILITY_AVAILABLE", True, raising=False)
    monkeypatch.setattr(wrapper_mod.VolatilityWrapper, "symbol_dirs", [], raising=False)

    wrapper.run_plugin("handles.Handles", pid=4376)
    wrapper.run_plugin("handles.Handles", pid=100)  # different pid → must re-run

    assert calls == [4376, 100], "different pid must not collide on cache"
