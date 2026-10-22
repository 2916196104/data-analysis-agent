"""Compatibility import for :mod:`data_analysis_agent.reviews.labeling`."""

import sys

from .reviews import labeling as _implementation

# Preserve module identity, including legacy monkeypatches and private imports.
sys.modules[__name__] = _implementation
