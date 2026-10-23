"""IDE 直接运行 CLI 文件时补充包上下文。"""

import sys
from pathlib import Path


def prepare_direct_run(script: str) -> str:
    script_path = Path(script).resolve()
    package_dir = next(parent for parent in script_path.parents if parent.name == "data_analysis_agent")
    sys.path.insert(0, str(package_dir.parent))
    # 直接点击 Run 时应说明可用命令，而不是启动业务处理。
    if len(sys.argv) == 1:
        sys.argv.append("--help")
    return ".".join(script_path.parent.relative_to(package_dir.parent).parts)
