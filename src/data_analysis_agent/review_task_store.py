"""Compatibility import for :mod:`data_analysis_agent.jobs.store`."""

import sys

from .jobs import store as _implementation

# Preserve module identity, including legacy monkeypatches and private imports.
sys.modules[__name__] = _implementation
