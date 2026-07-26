"""Tests for DiskResultCache load/save/delete."""


from zero.core.result_cache import DiskResultCache


def test_save_load_and_kwargs_isolation(tmp_path):
    img = tmp_path / "mem.raw"
    img.write_bytes(b"x" * 64)
    cache = DiskResultCache(tmp_path / "results", enabled=True)
    cols = ["A", "B"]
    assert cache.save(str(img), "linux.pslist.PsList", cols, [("1", "2")], kwargs={})
    assert cache.save(
        str(img), "linux.pslist.PsList", cols, [("9", "9")], kwargs={"pid": 7}
    )

    bare = cache.load(str(img), "linux.pslist.PsList", kwargs={})
    assert bare is not None
    assert bare[1] == [("1", "2")]

    with_pid = cache.load(str(img), "linux.pslist.PsList", kwargs={"pid": 7})
    assert with_pid is not None
    assert with_pid[1] == [("9", "9")]

    assert cache.load(str(img), "linux.pslist.PsList", kwargs={"pid": 99}) is None


def test_delete_plugin_and_clear_image(tmp_path):
    img = tmp_path / "mem.raw"
    img.write_bytes(b"y" * 32)
    cache = DiskResultCache(tmp_path / "results", enabled=True)
    cols = ["C"]
    cache.save(str(img), "linux.pslist.PsList", cols, [("1",)], kwargs={})
    cache.save(str(img), "linux.pslist.PsList", cols, [("2",)], kwargs={"pid": 1})
    cache.save(str(img), "linux.bash.Bash", cols, [("3",)], kwargs={})

    n = cache.delete(str(img), "linux.pslist.PsList", kwargs=None)
    assert n == 2
    assert cache.load(str(img), "linux.pslist.PsList", kwargs={}) is None
    assert cache.load(str(img), "linux.bash.Bash", kwargs={}) is not None

    n2 = cache.clear_image(str(img))
    assert n2 >= 1
    assert cache.load(str(img), "linux.bash.Bash", kwargs={}) is None


def test_save_writes_meta_json(tmp_path):
    img = tmp_path / "mem.raw"
    img.write_bytes(b"z" * 16)
    cache = DiskResultCache(tmp_path / "results", enabled=True)
    assert cache.save(str(img), "linux.pslist.PsList", ["A"], [("1",)], kwargs={"pid": 3})
    metas = list((tmp_path / "results").rglob("*.meta.json"))
    assert len(metas) == 1
    import json

    meta = json.loads(metas[0].read_text(encoding="utf-8"))
    assert meta["plugin"] == "linux.pslist.PsList"
    assert meta["kwargs"] == {"pid": 3}
    assert meta["row_count"] == 1


def test_delete_removes_meta(tmp_path):
    img = tmp_path / "mem.raw"
    img.write_bytes(b"w" * 8)
    cache = DiskResultCache(tmp_path / "results", enabled=True)
    cache.save(str(img), "linux.pslist.PsList", ["A"], [("1",)], kwargs={})
    assert list((tmp_path / "results").rglob("*.meta.json"))
    cache.delete(str(img), "linux.pslist.PsList", kwargs={})
    assert list((tmp_path / "results").rglob("*.meta.json")) == []
    assert list((tmp_path / "results").rglob("*.csv")) == []
