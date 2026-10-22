"""Compatibility import for :mod:`data_analysis_agent.core.config`."""

import sys

from .core import config as _implementation

# Preserve module identity, including legacy monkeypatches and private imports.
sys.modules[__name__] = _implementation
