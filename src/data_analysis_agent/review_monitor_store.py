"""Compatibility import for :mod:`data_analysis_agent.reviews.monitor_store`."""

import sys

from .reviews import monitor_store as _implementation

# Preserve module identity, including legacy monkeypatches and private imports.
sys.modules[__name__] = _implementation
