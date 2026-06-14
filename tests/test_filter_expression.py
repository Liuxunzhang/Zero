"""Tests for ``zero.utils.filter_expression``.

These are pure-function tests — no engine, no network, no disk.  They pin the
contract that ``Vol3Engine.get_results`` and the web filter path rely on.
"""

from __future__ import annotations

import pytest

from zero.utils.filter_expression import AdvancedFilter, FilterParser, Operator


COLUMNS = ["PID", "ImageFileName", "Path", "Memory"]
ROWS = [
    (4, "System", "C:\\Windows\\system32", 1024),
    (443, "svchost.exe", "C:\\Windows\\System32\\svchost.exe", 8192),
    (999, "malware.exe", "C:\\Users\\evil\\malware.exe", 4096),
    (1000, "clean.exe", "C:\\Program Files\\clean.exe", 16384),
]


def _run(expr: str, columns=COLUMNS, rows=ROWS):
    af = AdvancedFilter(columns)
    assert af.set_expression(expr), f"expected valid expr, got error: {af.get_error()}"
    return af.filter_rows(rows)


# ── Simple vs advanced detection ─────────────────────────────────────

def test_simple_text_returns_none_expression():
    """A bare keyword without ``-op`` should not parse as an advanced filter."""
    af = AdvancedFilter(COLUMNS)
    # ``svchost`` has no ``-xxx`` operator token and no logic op → treated as plain text.
    assert af.set_expression("svchost") is True
    assert af.has_expression() is False


def test_empty_expression_is_valid():
    af = AdvancedFilter(COLUMNS)
    assert af.set_expression("") is True
    assert af.has_expression() is False
    assert af.filter_rows(ROWS) == ROWS  # passes everything through


# ── Each operator ────────────────────────────────────────────────────

def test_eq_string():
    matched = _run("ImageFileName -eq \"svchost.exe\"")
    assert [r[0] for r in matched] == [443]


def test_eq_number_typed():
    matched = _run("PID -eq 443")
    assert [r[0] for r in matched] == [443]


def test_ne_string():
    matched = _run("ImageFileName -ne \"System\"")
    assert {r[0] for r in matched} == {443, 999, 1000}


def test_gt_number():
    matched = _run("PID -gt 500")
    assert sorted(r[0] for r in matched) == [999, 1000]


def test_lt_number():
    matched = _run("Memory -lt 4096")
    assert sorted(r[0] for r in matched) == [4]


def test_ge_number():
    matched = _run("Memory -ge 4096")
    assert sorted(r[0] for r in matched) == [443, 999, 1000]


def test_le_number():
    matched = _run("PID -le 443")
    assert sorted(r[0] for r in matched) == [4, 443]


def test_contain_case_insensitive():
    matched = _run("ImageFileName -contain EXE")
    assert sorted(r[0] for r in matched) == [443, 999, 1000]


def test_notcontain():
    matched = _run("Path -notcontain Windows")
    assert sorted(r[0] for r in matched) == [999, 1000]


def test_match_regex():
    # Match the C:\Users\...\malware.exe path.  Using a raw Python string so
    # the backslashes survive into the filter expression verbatim.
    matched = _run(r'Path -match "Users.*\.exe"')
    assert [r[0] for r in matched] == [999]


def test_startswith():
    matched = _run("Path -startswith \"C:\\\\Windows\"")
    assert sorted(r[0] for r in matched) == [4, 443]


def test_endswith():
    matched = _run("Path -endswith \".exe\"")
    assert sorted(r[0] for r in matched) == [443, 999, 1000]


# ── Logical combinations ─────────────────────────────────────────────

def test_and_combination():
    # Memory > 4096 AND < 16384: only svchost (8192) qualifies.
    matched = _run("Memory -gt 4096 && Memory -lt 16384")
    assert [r[0] for r in matched] == [443]


def test_or_combination():
    matched = _run("PID -eq 4 || PID -eq 999")
    assert sorted(r[0] for r in matched) == [4, 999]


def test_three_conditions_and():
    matched = _run("Memory -gt 2000 && ImageFileName -contain exe && PID -lt 1000")
    assert sorted(r[0] for r in matched) == [443, 999]


# ── Column validation ────────────────────────────────────────────────

def test_unknown_column_rejected_when_columns_known():
    af = AdvancedFilter(COLUMNS)
    assert af.set_expression("Nonexistent -eq 1") is False
    assert "Unknown column" in (af.get_error() or "")


def test_unknown_column_allowed_when_columns_unknown():
    """No column hints → the parser should accept any identifier."""
    af = AdvancedFilter([])  # empty columns list
    assert af.set_expression("Anything -eq 1") is True


# ── Error cases ──────────────────────────────────────────────────────

def test_two_barewords_without_operator_is_plain_text():
    """``PID 100`` has no ``-op`` token and no logic op, so the parser treats
    it as plain text rather than erroring.  This documents the real fallback
    path used by ``Vol3Engine.get_results`` for plain substring filters."""
    af = AdvancedFilter(COLUMNS)
    assert af.set_expression("PID 100") is True
    assert af.has_expression() is False  # no advanced expression built


def test_trailing_logic_operator():
    af = AdvancedFilter(COLUMNS)
    assert af.set_expression("PID -eq 1 &&") is False


def test_get_error_cleared_on_success():
    af = AdvancedFilter(COLUMNS)
    af.set_expression("broken expr -eq")  # invalid
    assert af.get_error() is not None
    af.set_expression("PID -eq 1")  # valid
    assert af.get_error() is None


def test_clear_drops_expression():
    af = AdvancedFilter(COLUMNS)
    af.set_expression("PID -eq 1")
    assert af.has_expression()
    af.clear()
    assert not af.has_expression()
    assert af.filter_rows(ROWS) == ROWS


# ── Evaluator-level robustness ───────────────────────────────────────

def test_short_row_does_not_crash():
    """Rows shorter than the column list must not raise IndexError."""
    af = AdvancedFilter(["A", "B", "C"])
    af.set_expression("C -eq 1")
    # Row with only 2 cells — C is out of range, should evaluate to False.
    assert af.filter_rows([(1, 2)]) == []


def test_numeric_comparison_against_text_cell():
    """If the cell isn't numeric, ``-gt`` must return False, not raise."""
    af = AdvancedFilter(["Name", "Count"])
    af.set_expression("Count -gt 5")
    assert af.filter_rows([("a", "not-a-number")]) == []
