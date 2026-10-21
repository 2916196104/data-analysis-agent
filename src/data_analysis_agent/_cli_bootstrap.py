"""Supply package context when an IDE runs a CLI file directly."""

import sys
from pathlib import Path


def prepare_direct_run(script: str) -> str:
    package_dir = Path(script).resolve().parent
    sys.path.insert(0, str(package_dir.parent))
    # A bare Run click should explain the commands, not start business work.
    if len(sys.argv) == 1:
        sys.argv.append("--help")
    return package_dir.name
