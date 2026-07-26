"""Tests for advanced filter expression parser/evaluator."""

from zero.utils.filter_expression import AdvancedFilter, FilterParser


COLUMNS = ["PID", "NAME", "PATH"]
ROWS = [
    ("1", "systemd", "/usr/lib/systemd"),
    ("100", "sshd", "/usr/sbin/sshd"),
    ("200", "bash", "/bin/bash"),
    ("300", "sshd-session", "/usr/sbin/sshd"),
]


def test_simple_text_returns_none_from_parser():
    parser = FilterParser(COLUMNS)
    assert parser.parse("sshd") is None
    assert parser.parse("") is None


def test_set_expression_false_for_simple_text():
    af = AdvancedFilter(COLUMNS)
    assert af.set_expression("sshd") is False
    assert af.has_expression() is False


def test_eq_and_contain():
    af = AdvancedFilter(COLUMNS)
    assert af.set_expression('NAME -eq "bash"')
    out = af.filter_rows(ROWS)
    assert out == [("200", "bash", "/bin/bash")]

    assert af.set_expression('NAME -contain "sshd"')
    out = af.filter_rows(ROWS)
    assert len(out) == 2


def test_numeric_compare():
    af = AdvancedFilter(COLUMNS)
    assert af.set_expression("PID -gt 100")
    out = af.filter_rows(ROWS)
    assert [r[0] for r in out] == ["200", "300"]


def test_and_or():
    af = AdvancedFilter(COLUMNS)
    assert af.set_expression('NAME -contain "sshd" && PID -gt 150')
    out = af.filter_rows(ROWS)
    assert out == [("300", "sshd-session", "/usr/sbin/sshd")]

    assert af.set_expression('NAME -eq "bash" || NAME -eq "systemd"')
    out = af.filter_rows(ROWS)
    assert len(out) == 2


def test_match_regex():
    af = AdvancedFilter(COLUMNS)
    assert af.set_expression(r'PATH -match "/usr/.*"')
    out = af.filter_rows(ROWS)
    assert all("/usr/" in r[2] for r in out)
    assert len(out) == 3


def test_unknown_operator_errors():
    af = AdvancedFilter(COLUMNS)
    ok = af.set_expression("NAME -foo bar")
    assert ok is False
    assert af.get_error()


def test_unknown_column_errors():
    af = AdvancedFilter(COLUMNS)
    assert af.set_expression('NOPE -eq "x"') is False
    assert "Unknown column" in af.get_error()


def test_column_and_value_matching_is_case_insensitive():
    af = AdvancedFilter(COLUMNS)
    assert af.set_expression('name -eq "BASH"')
    assert af.filter_rows(ROWS) == [("200", "bash", "/bin/bash")]


def test_reused_filter_recompiles_after_new_expression():
    """Regression guard for the compiled-condition memo."""
    af = AdvancedFilter(COLUMNS)
    assert af.set_expression('NAME -eq "bash"')
    assert len(af.filter_rows(ROWS)) == 1
    assert af.set_expression('NAME -eq "sshd"')
    out = af.filter_rows(ROWS)
    assert out == [("100", "sshd", "/usr/sbin/sshd")]


def test_numeric_condition_rejects_non_numeric_cells():
    af = AdvancedFilter(COLUMNS)
    assert af.set_expression("NAME -gt 5")
    # No NAME value parses as a number, so nothing matches.
    assert af.filter_rows(ROWS) == []


def test_numeric_operators_full_range():
    af = AdvancedFilter(COLUMNS)
    for expr, expected in [
        ("PID -eq 100", ["100"]),
        ("PID -ne 100", ["1", "200", "300"]),
        ("PID -lt 200", ["1", "100"]),
        ("PID -ge 200", ["200", "300"]),
        ("PID -le 100", ["1", "100"]),
    ]:
        assert af.set_expression(expr), expr
        assert [r[0] for r in af.filter_rows(ROWS)] == expected, expr


def test_string_operators_full_range():
    af = AdvancedFilter(COLUMNS)
    assert af.set_expression('NAME -startswith "ss"')
    assert [r[1] for r in af.filter_rows(ROWS)] == ["sshd", "sshd-session"]

    assert af.set_expression('PATH -endswith "/bash"')
    assert [r[1] for r in af.filter_rows(ROWS)] == ["bash"]

    assert af.set_expression('NAME -notcontain "sshd"')
    assert [r[1] for r in af.filter_rows(ROWS)] == ["systemd", "bash"]


def test_invalid_regex_matches_nothing():
    af = AdvancedFilter(COLUMNS)
    assert af.set_expression(r'PATH -match "([unclosed"')
    assert af.filter_rows(ROWS) == []


def test_logic_is_left_to_right_without_precedence():
    af = AdvancedFilter(COLUMNS)
    # (NAME -eq "bash" || NAME -eq "systemd") && PID -gt 150 -> only bash (200)
    assert af.set_expression('NAME -eq "bash" || NAME -eq "systemd" && PID -gt 150')
    assert [r[1] for r in af.filter_rows(ROWS)] == ["bash"]
