"""Compatibility import for :mod:`data_analysis_agent.sales.loader`."""

import sys

from .sales import loader as _implementation

# Preserve module identity, including legacy monkeypatches and private imports.
sys.modules[__name__] = _implementation
