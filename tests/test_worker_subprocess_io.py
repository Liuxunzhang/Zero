"""Subprocess worker I/O regressions (no real Volatility involved).

Both tests drive ``VolatilityWrapper._run_plugin_via_subprocess`` against a fake
worker script so the real pipe semantics are exercised:

* trailing stdout lines buffered at child exit must still be read
  (otherwise a successful plugin reports "插件执行失败，未返回结果")
* stderr must be drained while the child runs, or a child writing more than the
  pipe capacity blocks forever and the stall timer misreports "插件疑似卡死"
"""

import json
import sys
import textwrap
import threading

import pytest

from zero.core.wrapper import VolatilityWrapper


def _bare_wrapper(script_path, stall_timeout=10):
    """A wrapper stripped to just what the subprocess read loop touches."""
    w = VolatilityWrapper.__new__(VolatilityWrapper)
    w.image_path = "/nonexistent/mem.raw"
    w.symbol_dirs = []
    w.plugin_timeout_seconds = 60
    w.stall_timeout_seconds = stall_timeout
    w.progress_log_throttle_seconds = 0.0
    w.terminate_grace_seconds = 1.0
    w._worker_process = None
    w._state_lock = threading.Lock()
    w._plugin_running = True
    w._cancel_requested = threading.Event()
    w._build_worker_command = lambda plugin, kwargs=None: [
        sys.executable,
        str(script_path),
    ]
    return w


def _write_worker(tmp_path, body):
    script = tmp_path / "fake_worker.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    return script


def test_trailing_stdout_lines_are_drained(tmp_path):
    """Many events emitted back-to-back then immediate exit: result survives."""
    rows = [[str(i), f"proc{i}"] for i in range(50)]
    script = _write_worker(
        tmp_path,
        f"""
        import json, sys
        for i in range(200):
            sys.stdout.write(json.dumps({{"type": "progress", "payload": f"step {{i}}"}}) + "\\n")
        sys.stdout.write(json.dumps(
            {{"type": "result", "payload": {{"columns": ["PID", "NAME"], "rows": {json.dumps(rows)}}}}}
        ) + "\\n")
        sys.stdout.flush()
        sys.exit(0)
        """,
    )
    w = _bare_wrapper(script)

    columns, data_rows = w._run_plugin_via_subprocess("fake.Plugin")

    assert columns == ["PID", "NAME"]
    assert len(data_rows) == 50
    assert data_rows[0] == ("0", "proc0")


def test_noisy_stderr_does_not_deadlock(tmp_path):
    """A worker writing far more than the pipe capacity to stderr still finishes."""
    script = _write_worker(
        tmp_path,
        """
        import json, sys
        # 512 KiB — well past the 64 KiB pipe buffer that would block the child.
        noise = "vol3 warning: unreadable page\\n" * 20000
        sys.stderr.write(noise)
        sys.stderr.flush()
        sys.stdout.write(json.dumps(
            {"type": "result", "payload": {"columns": ["A"], "rows": [["1"]]}}
        ) + "\\n")
        sys.stdout.flush()
        sys.exit(0)
        """,
    )
    w = _bare_wrapper(script, stall_timeout=20)

    columns, data_rows = w._run_plugin_via_subprocess("fake.Plugin")

    assert columns == ["A"]
    assert data_rows == [("1",)]


def test_stderr_capture_is_bounded_and_keeps_tail(tmp_path):
    """Error path surfaces the tail of stderr, not an unbounded buffer."""
    script = _write_worker(
        tmp_path,
        """
        import sys
        sys.stderr.write("filler line\\n" * 50000)
        sys.stderr.write("FinalRealError: symbol table mismatch\\n")
        sys.stderr.flush()
        sys.exit(1)
        """,
    )
    w = _bare_wrapper(script, stall_timeout=20)

    with pytest.raises(ValueError) as excinfo:
        w._run_plugin_via_subprocess("fake.Plugin")

    message = str(excinfo.value)
    assert "symbol table" in message


def test_worker_heartbeat_prevents_false_stall_timeout(tmp_path):
    """Silent plugin work stays alive when the worker itself still responds."""
    script = _write_worker(
        tmp_path,
        """
        import json, sys, time
        for _ in range(5):
            sys.stdout.write(json.dumps(
                {"type": "heartbeat", "payload": None}
            ) + "\\n")
            sys.stdout.flush()
            time.sleep(0.3)
        sys.stdout.write(json.dumps(
            {"type": "result", "payload": {"columns": ["A"], "rows": [["done"]]}}
        ) + "\\n")
        sys.stdout.flush()
        """,
    )
    w = _bare_wrapper(script, stall_timeout=1)

    columns, data_rows = w._run_plugin_via_subprocess("fake.Plugin")

    assert columns == ["A"]
    assert data_rows == [("done",)]
