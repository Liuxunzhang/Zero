"""Shared pytest fixtures for the Zero test suite.

Tests run against the in-tree packages (``zero``, ``web.backend``) so they
exercise real code paths.  Heavy external dependencies — the Volatility 3
engine, the OpenAI client, the on-disk project layout under ``.zero/`` — are
isolated via fixtures that point stores at per-test ``tmp_path`` directories.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

# Ensure the project root is importable regardless of pytest invocation cwd.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture
def ai_data_dir(tmp_path: Path) -> Path:
    """Isolated ``.zero/ai`` directory for AI service / store tests."""
    data_dir = tmp_path / ".zero" / "ai"
    data_dir.mkdir(parents=True)
    return data_dir


@pytest.fixture
def conv_dir(tmp_path: Path, monkeypatch) -> Path:
    """Redirect the module-level conversation store at a tmp directory.

    The store resolves its directory at *import* time, so we patch the
    module attributes in place and reload to pick up the new path.
    """
    conv_dir = tmp_path / ".zero" / "ai" / "conversations"
    conv_dir.mkdir(parents=True)
    from web.backend.services import conversation_store as mod
    monkeypatch.setattr(mod, "_CONV_DIR", conv_dir)
    monkeypatch.setattr(mod, "_INDEX_FILE", conv_dir / "index.json")
    return conv_dir
