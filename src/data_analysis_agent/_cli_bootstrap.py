"""Supply package context when an IDE runs a CLI file directly."""

import sys
from pathlib import Path


def prepare_direct_run(script: str) -> str:
    script_path = Path(script).resolve()
    package_dir = next(parent for parent in script_path.parents if parent.name == "data_analysis_agent")
    sys.path.insert(0, str(package_dir.parent))
    # A bare Run click should explain the commands, not start business work.
    if len(sys.argv) == 1:
        sys.argv.append("--help")
    return ".".join(script_path.parent.relative_to(package_dir.parent).parts)
