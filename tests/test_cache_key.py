"""Tests for zero.core.cache_key."""

from zero.core.cache_key import (
    NOPARAMS_DIGEST,
    image_identity,
    kwargs_digest,
    make_cache_key,
    normalize_kwargs,
)


def test_normalize_drops_empty():
    assert normalize_kwargs({"pid": "", "name": None, "x": "  "}) == {}
    assert normalize_kwargs({"pid": 1, "name": " a "}) == {"pid": 1, "name": "a"}


def test_kwargs_digest_order_independent():
    assert kwargs_digest({"pid": 1, "name": "a"}) == kwargs_digest({"name": "a", "pid": 1})


def test_kwargs_digest_empty_is_noparams():
    assert kwargs_digest({}) == NOPARAMS_DIGEST
    assert kwargs_digest(None) == NOPARAMS_DIGEST
    assert kwargs_digest({"pid": 1}) != NOPARAMS_DIGEST


def test_make_cache_key_differs_by_kwargs(tmp_path):
    img = tmp_path / "a.raw"
    img.write_bytes(b"data")
    k0 = make_cache_key(str(img), "linux.pslist.PsList", {})
    k1 = make_cache_key(str(img), "linux.pslist.PsList", {"pid": 1})
    assert k0 != k1
    assert k0 == make_cache_key(str(img), "linux.pslist.PsList", None)


def test_image_identity_changes_with_content(tmp_path):
    img = tmp_path / "dump.raw"
    img.write_bytes(b"aaa")
    id1 = image_identity(str(img))
    img.write_bytes(b"bbbb")
    id2 = image_identity(str(img))
    assert id1 != id2


def test_normalize_list_and_nested():
    n = normalize_kwargs({"tags": ["b", "a", ""], "meta": {"z": 1, "a": 2}})
    assert n["tags"] == ["b", "a"]
    assert n["meta"] == {"a": 2, "z": 1}


def test_bool_and_int_preserved():
    n = normalize_kwargs({"flag": True, "n": 0})
    assert n["flag"] is True
    assert n["n"] == 0
    assert kwargs_digest({"flag": True}) != kwargs_digest({"flag": False})
