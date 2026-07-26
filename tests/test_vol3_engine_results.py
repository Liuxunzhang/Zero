"""Tests for Vol3Engine.get_results filter/sort LRU (no real Volatility)."""

import threading
from collections import OrderedDict

from zero.engines.vol3_engine import Vol3Engine


def _bare_engine(rows, columns=None, max_entries=64, max_rows=2_000_000):
    eng = Vol3Engine.__new__(Vol3Engine)
    eng._lock = threading.RLock()
    eng._columns = columns or ["PID", "NAME"]
    eng._rows = rows
    eng._current_plugin = "linux.pslist.PsList"
    eng._results_version = 1
    eng._fs_cache = OrderedDict()
    eng._results_query_cache_max_entries = max_entries
    eng._results_query_cache_max_rows = max_rows
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


def test_plain_pagination_is_not_cached_or_copied():
    rows = [(str(i), f"proc{i}") for i in range(30)]
    eng = _bare_engine(rows)

    page2 = eng.get_results(page=2, page_size=10)

    assert page2["total"] == 30
    assert page2["total_pages"] == 3
    assert page2["rows"][0] == ["10", "proc10"]
    # Nothing to recompute without filter/sort, so nothing is cached.
    assert len(eng._fs_cache) == 0


def test_sort_without_filter_leaves_source_rows_untouched():
    rows = [("3", "c"), ("1", "a"), ("2", "b")]
    eng = _bare_engine(rows)

    out = eng.get_results(sort_column="PID", page=1, page_size=10)

    assert [r[0] for r in out["rows"]] == ["1", "2", "3"]
    # The engine's canonical list must not be sorted in place.
    assert rows == [("3", "c"), ("1", "a"), ("2", "b")]


def test_sort_desc_and_numeric_ordering():
    rows = [("10", "x"), ("9", "y"), ("100", "z")]
    eng = _bare_engine(rows)

    out = eng.get_results(sort_column="PID", sort_desc=True, page=1, page_size=10)

    # Digit strings sort numerically, not lexicographically.
    assert [r[0] for r in out["rows"]] == ["100", "10", "9"]


def test_row_budget_evicts_before_entry_cap():
    rows = [(str(i), f"p{i}") for i in range(100)]
    # Entry cap is generous; the row budget is the binding constraint.
    eng = _bare_engine(rows, max_entries=64, max_rows=120)

    eng.get_results(filter_text="p", page=1, page_size=10)        # 100 rows
    assert len(eng._fs_cache) == 1
    eng.get_results(filter_text="p1", page=1, page_size=10)       # 11 rows
    eng.get_results(filter_text="p2", page=1, page_size=10)       # 11 rows

    cached_rows = sum(e["total"] for e in eng._fs_cache.values())
    assert cached_rows <= 120
    filters = {k[1] for k in eng._fs_cache}
    assert "p" not in filters       # the 100-row entry was evicted first
    assert "p2" in filters


# ── export_results with view params ─────────────────────────────────


def _export_engine(rows, tmp_path, monkeypatch, columns=None):
    """Bare engine + EXPORT_DIR redirected to tmp_path."""
    from zero import config

    monkeypatch.setattr(config, "EXPORT_DIR", str(tmp_path))
    return _bare_engine(rows, columns=columns)


def _read_csv(path):
    import csv

    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


def test_export_full_set_no_suffix(tmp_path, monkeypatch):
    rows = [(str(i), f"proc{i}") for i in range(10)]
    eng = _export_engine(rows, tmp_path, monkeypatch)

    path = eng.export_results("csv")

    assert path and "_filtered" not in path
    data = _read_csv(path)
    assert data[0] == ["PID", "NAME"]
    assert len(data) - 1 == 10


def test_export_filtered_view(tmp_path, monkeypatch):
    rows = [("1", "sshd"), ("2", "bash"), ("3", "sshd-session")]
    eng = _export_engine(rows, tmp_path, monkeypatch)

    path = eng.export_results("csv", filter_text="sshd")

    assert path and "_filtered" in path
    data = _read_csv(path)
    assert len(data) - 1 == 2
    assert all("sshd" in r[1] for r in data[1:])


def test_export_sorted_view_order_and_suffix(tmp_path, monkeypatch):
    rows = [("10", "x"), ("9", "y"), ("100", "z")]
    eng = _export_engine(rows, tmp_path, monkeypatch)

    path = eng.export_results("csv", sort_column="PID", sort_desc=True)

    assert path and "_filtered" in path
    data = _read_csv(path)
    assert [r[0] for r in data[1:]] == ["100", "10", "9"]


def test_export_filter_no_match_header_only(tmp_path, monkeypatch):
    rows = [("1", "sshd")]
    eng = _export_engine(rows, tmp_path, monkeypatch)

    path = eng.export_results("csv", filter_text="nomatch")

    data = _read_csv(path)
    assert data == [["PID", "NAME"]]


def test_export_truncation_applies_after_filter(tmp_path, monkeypatch):
    from zero.engines import vol3_engine

    rows = [(str(i), "keep" if i % 2 == 0 else "drop") for i in range(100)]
    eng = _export_engine(rows, tmp_path, monkeypatch)
    monkeypatch.setattr(vol3_engine, "_MAX_EXPORT_ROWS", 10)

    path = eng.export_results("csv", filter_text="keep")

    data = _read_csv(path)
    # 50 rows matched, capped at 10 post-filter.
    assert len(data) - 1 == 10
    assert all(r[1] == "keep" for r in data[1:])
