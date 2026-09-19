from __future__ import annotations

import os
from pathlib import Path
import shlex
import sys


def report_command_argv(project_dir: Path | None = None) -> list[str]:
    configured = (os.getenv("MORNING_REPORT_CMD") or "").strip()
    if configured:
        args = shlex.split(configured)
        if args and args[0] in {"python", "python3"}:
            args[0] = sys.executable
        return args
    project_dir = project_dir or Path.cwd()
    return [sys.executable, str(project_dir / "slack_morning_report.py")]
