"""Compatibility import for :mod:`data_analysis_agent.sales.analysis`."""

import sys

from .sales import analysis as _implementation

# Preserve module identity, including legacy monkeypatches and private imports.
sys.modules[__name__] = _implementation
