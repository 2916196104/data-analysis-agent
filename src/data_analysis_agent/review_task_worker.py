"""Compatibility import for :mod:`data_analysis_agent.jobs.worker`."""

import sys

from .jobs import worker as _implementation

# Preserve module identity, including legacy monkeypatches and private imports.
sys.modules[__name__] = _implementation
