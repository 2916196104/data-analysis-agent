"""Compatibility import for :mod:`data_analysis_agent.reviews.analytics`."""

import sys

from .reviews import analytics as _implementation

# Preserve module identity, including legacy monkeypatches and private imports.
sys.modules[__name__] = _implementation
