"""Compatibility import for :mod:`data_analysis_agent.cli.labeling`."""

import sys

if __name__ == "__main__" and not __package__:
    from _cli_bootstrap import prepare_direct_run

    __package__ = prepare_direct_run(__file__)

from .cli import labeling as _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())

# Preserve module identity, including legacy monkeypatches and private imports.
sys.modules[__name__] = _implementation
