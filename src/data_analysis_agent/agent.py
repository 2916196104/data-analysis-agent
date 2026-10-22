"""Compatibility import for :mod:`data_analysis_agent.agents.sales`."""

import sys

from .agents import sales as _implementation

# Preserve module identity, including legacy monkeypatches and private imports.
sys.modules[__name__] = _implementation
