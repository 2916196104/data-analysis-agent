"""Compatibility import for :mod:`data_analysis_agent.reviews.loader`."""

import sys

from .reviews import loader as _implementation

# Preserve module identity, including legacy monkeypatches and private imports.
sys.modules[__name__] = _implementation
