"""VolatilityWrapper in-memory result cache is a bounded LRU (no real Volatility)."""

import threading

from zero.core.wrapper import VolatilityWrapper


def _bare_wrapper(max_entries=3):
    """Only the fields the cache accessors touch — skips vol3 initialization."""
    from collections import OrderedDict

    w = VolatilityWrapper.__new__(VolatilityWrapper)
    w._cache = OrderedDict()
    w._memory_cache_max = max_entries
    w._state_lock = threading.Lock()
    w._cache_stats = {}
    return w


def _payload(n):
    return (["A"], [(str(n),)])


def test_evicts_least_recently_used():
    w = _bare_wrapper(max_entries=3)
    for i in range(3):
        w._cache_put(f"k{i}", _payload(i))
    assert list(w._cache) == ["k0", "k1", "k2"]

    w._cache_put("k3", _payload(3))

    assert "k0" not in w._cache
    assert list(w._cache) == ["k1", "k2", "k3"]
    assert len(w._cache) == 3


def test_read_refreshes_recency():
    w = _bare_wrapper(max_entries=3)
    for i in range(3):
        w._cache_put(f"k{i}", _payload(i))

    # Touch the oldest entry so the next insert evicts k1 instead of k0.
    assert w._cache_get("k0") == _payload(0)
    w._cache_put("k3", _payload(3))

    assert "k0" in w._cache
    assert "k1" not in w._cache


def test_reinsert_does_not_grow_past_cap():
    w = _bare_wrapper(max_entries=2)
    w._cache_put("k0", _payload(0))
    w._cache_put("k0", _payload(99))
    assert len(w._cache) == 1
    assert w._cache_get("k0") == _payload(99)


def test_get_missing_returns_none():
    w = _bare_wrapper()
    assert w._cache_get("absent") is None
