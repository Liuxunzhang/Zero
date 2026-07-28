import json
import queue
import stat
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("yara_x")

from zero.engines.yarax_engine import _scan_worker
from zero.yarax.compiler import compile_package, inspect_package
from zero.yarax.errors import ConflictError, ValidationError
from zero.yarax.store import YaraXStore
from zero.yarax.zipio import safe_extract_zip


def write_package(root: Path, main: str, extra=None, manifest=None):
    root.mkdir(parents=True)
    (root / "main.yar").write_text(main, encoding="utf-8")
    for name, content in (extra or {}).items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    if manifest:
        (root / "zero-yara.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_compile_include_and_structured_diagnostic(tmp_path):
    root = tmp_path / "rules"
    write_package(
        root,
        'include "inc/helper.yar"\n',
        {"inc/helper.yar": "rule bad { condition: missing_name }\n"},
    )
    with pytest.raises(ValidationError) as caught:
        compile_package(root)
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic["code"] == "E009"
    assert diagnostic["file"] == "inc/helper.yar"
    assert diagnostic["line"] == 1
    assert diagnostic["span"]["end"] > diagnostic["span"]["start"]


@pytest.mark.parametrize("include", ["/etc/passwd", "../outside.yar", "sub/../../x.yar"])
def test_include_cannot_escape_package(tmp_path, include):
    root = tmp_path / "rules"
    write_package(root, f'include "{include}"\n')
    with pytest.raises(ValidationError):
        inspect_package(root)


def test_include_cycle_is_rejected(tmp_path):
    root = tmp_path / "rules"
    write_package(
        root, 'include "other.yar"\n',
        {"other.yar": 'include "main.yar"\n'},
    )
    with pytest.raises(ValidationError, match="Cyclic include"):
        inspect_package(root)


def test_draft_revision_conflict_and_immutable_version(tmp_path):
    store = YaraXStore(tmp_path / "data")
    package = store.create_package(name="Local", package_id="local")
    draft = store.get_draft(package["id"])
    main = next(item for item in draft["files"] if item["path"] == "main.yar")
    saved = store.write_draft_file(
        package["id"], "main.yar",
        'rule hit { strings: $a = "needle" condition: $a }\n',
        base_revision=draft["revision"], file_sha=main["sha256"],
    )
    with pytest.raises(ConflictError):
        store.write_draft_file(
            package["id"], "main.yar", "rule stale { condition: true }",
            base_revision=draft["revision"], file_sha=saved["sha256"],
        )
    version = store.commit_draft(package["id"], base_revision=saved["revision"])
    assert store.get_package(package["id"])["enabled"] is True
    assert Path(version["path"], "main.yar").read_text().startswith("rule hit")


def test_worker_emits_one_row_per_match_and_rule_only_rows(tmp_path):
    root = tmp_path / "rules"
    write_package(
        root,
        """
        rule with_strings : t1 {
          meta: author = "zero"
          strings: $a = "needle"
          condition: $a
        }
        rule rule_only { condition: true }
        """,
        manifest={
            "schema_version": 1, "name": "Rows", "version": "2",
            "entrypoints": ["main.yar"], "globals": {},
        },
    )
    image = tmp_path / "image.raw"
    image.write_bytes(b"needle--needle")
    events = queue.Queue()
    _scan_worker({
        "package_root": str(root), "manifest": inspect_package(root)["manifest"],
        "compiled_path": str(tmp_path / "compiled.yarac"),
        "image_path": str(image), "package_name": "Rows", "package_version": "2",
        "timeout": 10, "max_matches_per_pattern": 1000, "max_result_rows": 10,
        "fast_scan": False, "relaxed_regex": False, "globals": {},
    }, events)
    terminal = None
    while not events.empty():
        item = events.get()
        if item["type"] == "result":
            terminal = item
    assert terminal is not None
    assert [row[2] for row in terminal["rows"]].count("with_strings") == 2
    rule_only = next(row for row in terminal["rows"] if row[2] == "rule_only")
    assert rule_only[6:] == ["", None, None, None]


def test_worker_marks_truncated_results(tmp_path):
    root = tmp_path / "rules"
    write_package(root, 'rule x { strings: $a = "a" condition: $a }\n')
    image = tmp_path / "image.raw"
    image.write_bytes(b"a" * 20)
    events = queue.Queue()
    _scan_worker({
        "package_root": str(root), "manifest": inspect_package(root)["manifest"],
        "compiled_path": str(tmp_path / "compiled.yarac"),
        "image_path": str(image), "package_name": "Limit", "package_version": "1",
        "timeout": 10, "max_matches_per_pattern": 1000, "max_result_rows": 3,
        "fast_scan": False, "relaxed_regex": False, "globals": {},
    }, events)
    terminal = [item for item in list(events.queue) if item["type"] == "result"][0]
    assert len(terminal["rows"]) == 3
    assert terminal["truncated"] is True
    assert terminal["status"] == "truncated"


def test_zip_traversal_and_symlink_are_rejected(tmp_path):
    traversal = tmp_path / "traversal.zip"
    with zipfile.ZipFile(traversal, "w") as archive:
        archive.writestr("../outside.yar", "rule x { condition: true }")
    with pytest.raises(ValidationError):
        safe_extract_zip(traversal, tmp_path / "out1")

    symlink = tmp_path / "symlink.zip"
    info = zipfile.ZipInfo("link.yar")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(symlink, "w") as archive:
        archive.writestr(info, "target")
    with pytest.raises(ValidationError):
        safe_extract_zip(symlink, tmp_path / "out2")


def test_zip_duplicate_casefold_path_is_rejected(tmp_path):
    archive_path = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("A.yar", "rule a { condition: true }")
        archive.writestr("a.yar", "rule b { condition: true }")
    with pytest.raises(ValidationError, match="Duplicate normalized"):
        safe_extract_zip(archive_path, tmp_path / "out")
