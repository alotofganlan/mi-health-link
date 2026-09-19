from pathlib import Path
import sys

from xiaomi_health_sync.report_command import report_command_argv


def test_default_report_command_uses_current_python_and_project_script(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MORNING_REPORT_CMD", raising=False)
    assert report_command_argv(tmp_path) == [
        sys.executable,
        str(tmp_path / "slack_morning_report.py"),
    ]


def test_configured_python_command_uses_current_interpreter(monkeypatch) -> None:
    monkeypatch.setenv("MORNING_REPORT_CMD", "python custom_report.py --quiet")
    assert report_command_argv() == [sys.executable, "custom_report.py", "--quiet"]
