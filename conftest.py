"""
conftest.py — pytest configuration for Tesson test suite.

Adds 'src/' to sys.path so that package imports work as:
  from ingestion.x import ...
  from ledger import store

This aligns with pyproject.toml's [tool.setuptools.packages.find] where=["src"]
which declares src/ as the package root (not the project root).
"""

import sys
from pathlib import Path

# src/ is the package root per pyproject.toml — add it, not the project root
sys.path.insert(0, str(Path(__file__).parent / "src"))
