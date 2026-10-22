"""Compatibility import for :mod:`data_analysis_agent.api.schemas`."""

import sys

from .api import schemas as _implementation

# Preserve module identity, including legacy monkeypatches and private imports.
sys.modules[__name__] = _implementation
