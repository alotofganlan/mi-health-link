from __future__ import annotations

from datetime import date
import importlib.util
from pathlib import Path


REPORT_PATH = Path(__file__).parents[1] / "slack_morning_report.py"


def load_report_module():
    spec = importlib.util.spec_from_file_location("slack_report_signal", REPORT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_slack_trigger_includes_report_id_without_location(monkeypatch) -> None:
    report = load_report_module()
    monkeypatch.setenv("WAKE_REPORT_ID", "report-123")
    monkeypatch.setenv("WAKE_REPORT_KIND", "morning")
    body = report.render_trigger(date(2026, 9, 14))

    assert "report_id=report-123" in body
    assert "get_morning_context" in body
    assert "早安我的少年" in body
    assert "health_date=" not in body
    assert "latitude" not in body
    assert "longitude" not in body
