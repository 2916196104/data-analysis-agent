"""Compatibility import for :mod:`data_analysis_agent.reviews.monitoring`."""

import sys

from .reviews import monitoring as _implementation

# Preserve module identity, including legacy monkeypatches and private imports.
sys.modules[__name__] = _implementation
