"""Compatibility import for :mod:`data_analysis_agent.core.database`."""

import sys

from .core import database as _implementation

# Preserve module identity, including legacy monkeypatches and private imports.
sys.modules[__name__] = _implementation
