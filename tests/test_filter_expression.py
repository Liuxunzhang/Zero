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
