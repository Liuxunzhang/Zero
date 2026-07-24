"""Tests for Vol3Engine.get_results filter/sort LRU (no real Volatility)."""

import threading
from collections import OrderedDict

from zero.engines.vol3_engine import Vol3Engine


def _bare_engine(rows, columns=None, max_entries=64):
    eng = Vol3Engine.__new__(Vol3Engine)
    eng._lock = threading.RLock()
    eng._columns = columns or ["PID", "NAME"]
    eng._rows = rows
    eng._current_plugin = "linux.pslist.PsList"
    eng._results_version = 1
    eng._fs_cache = OrderedDict()
    eng._results_query_cache_max_entries = max_entries
    eng._image_loaded = True
    eng._plugin_busy = False
    return eng


def test_pagination_same_filter_one_fs_entry():
    rows = [(str(i), f"proc{i}") for i in range(25)]
    eng = _bare_engine(rows)
    page1 = eng.get_results(filter_text="proc1", page=1, page_size=5)
    page2 = eng.get_results(filter_text="proc1", page=2, page_size=5)
    assert page1["total"] == page2["total"]
    assert len(eng._fs_cache) == 1
    assert page1["page"] == 1
    assert page2["page"] == 2


def test_lru_eviction():
    rows = [(str(i), f"p{i}") for i in range(20)]
    eng = _bare_engine(rows, max_entries=2)
    eng.get_results(filter_text="p1", page=1, page_size=10)
    eng.get_results(filter_text="p2", page=1, page_size=10)
    assert len(eng._fs_cache) == 2
    eng.get_results(filter_text="p3", page=1, page_size=10)
    assert len(eng._fs_cache) == 2
    # p1 should be evicted (LRU)
    keys = list(eng._fs_cache.keys())
    filters = {k[1] for k in keys}
    assert "p1" not in filters
    assert "p3" in filters


def test_simple_filter_substring():
    rows = [("1", "sshd"), ("2", "bash"), ("3", "sshd-session")]
    eng = _bare_engine(rows)
    out = eng.get_results(filter_text="sshd", page=1, page_size=50)
    assert out["total"] == 2
