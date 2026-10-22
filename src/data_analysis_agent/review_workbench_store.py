"""Compatibility import for :mod:`data_analysis_agent.reviews.human_review`."""

import sys

from .reviews import human_review as _implementation

# Preserve module identity, including legacy monkeypatches and private imports.
sys.modules[__name__] = _implementation
