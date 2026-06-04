"""Volatility engine adapter layer for Zero."""

from .base import EngineBase, EngineResult
from .manager import EngineManager, get_manager

__all__ = ["EngineBase", "EngineResult", "EngineManager", "get_manager"]
