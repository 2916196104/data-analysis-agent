"""Compatibility import for :mod:`data_analysis_agent.reviews.scope`."""

import sys

from .reviews import scope as _implementation

# Preserve module identity, including legacy monkeypatches and private imports.
sys.modules[__name__] = _implementation
