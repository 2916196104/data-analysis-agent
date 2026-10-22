"""Compatibility import for :mod:`data_analysis_agent.agents.tools`."""

import sys

from .agents import tools as _implementation

# Preserve module identity, including legacy monkeypatches and private imports.
sys.modules[__name__] = _implementation
