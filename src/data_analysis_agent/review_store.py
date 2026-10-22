"""Compatibility import for :mod:`data_analysis_agent.reviews.store`."""

import sys

from .reviews import store as _implementation

# Preserve module identity, including legacy monkeypatches and private imports.
sys.modules[__name__] = _implementation
