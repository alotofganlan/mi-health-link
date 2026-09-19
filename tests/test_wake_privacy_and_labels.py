from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace

import mi_health_link.mcp_server as mcp_server
import mi_health_link.wake_report as wake_report
from mi_health_link.wake_context import WakeContextService


REPORT_PATH = Path(__file__).parents[1] / "slack_morning_report.py"


def load_report_module():
    spec = importlib.util.spec_from_file_location("slack_privacy_labels", REPORT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_nap_context_is_explicitly_labeled() -> None:
    class Store:
        def get_delivery(self, report_id):
            return {
                "id": report_id,
                "report_date": "2026-09-14",
                "report_kind": "nap",
                "includes_yesterday_health": False,
                "sleep_snapshot": {},
                "presence_id": None,
            }

    result = WakeContextService(store=Store(), weather=object()).get("report-1")

    assert result["title"] == "午睡简报"
    assert result["health_date"] is None


def test_report_runner_passes_nap_kind_to_trigger_process(monkeypatch) -> None:
    seen = {}

    def fake_run(*args, **kwargs):
        seen.update(kwargs["env"])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(wake_report.subprocess, "run", fake_run)

    assert wake_report.run_report_command("report-1", "nap") is True
    assert seen["WAKE_REPORT_ID"] == "report-1"
    assert seen["WAKE_REPORT_KIND"] == "nap"


def test_nap_trigger_body_includes_kind_and_nap_title(monkeypatch) -> None:
    report = load_report_module()
    monkeypatch.setenv("WAKE_REPORT_ID", "report-1")
    monkeypatch.setenv("WAKE_REPORT_KIND", "nap")

    body = report.render_trigger(datetime(2026, 9, 14).date())

    assert "report_kind=nap" in body
    assert "title=午睡简报" in body
    assert "title=早安我的少年" not in body


def test_mcp_server_disables_client_ip_access_log(monkeypatch) -> None:
    calls = {}
    settings = SimpleNamespace(host="127.0.0.1", port=8765)
    monkeypatch.setattr(mcp_server, "load_mcp_settings", lambda: settings)
    monkeypatch.setattr(mcp_server, "create_mcp_app", lambda value: object())

    class Uvicorn:
        @staticmethod
        def run(app, **kwargs):
            calls.update(kwargs)

    monkeypatch.setitem(__import__("sys").modules, "uvicorn", Uvicorn)
    httpx_logger = logging.getLogger("httpx")
    previous_level = httpx_logger.level
    httpx_logger.setLevel(logging.INFO)
    try:
        mcp_server.main()
    finally:
        configured_level = httpx_logger.level
        httpx_logger.setLevel(previous_level)

    assert calls["access_log"] is False
    assert configured_level >= logging.WARNING


def test_runtime_logs_do_not_embed_health_or_location_details() -> None:
    source_root = Path(__file__).parents[1] / "src" / "mi_health_link"
    sources = "\n".join(
        (source_root / name).read_text(encoding="utf-8")
        for name in (
            "wake_endpoints.py",
            "wake_report.py",
        )
    )
    prohibited_log_fields = (
        "city=%s",
        "district=%s",
        "country=%s",
        "device=%s",
        "unlock_at=%s",
        "wake_at=%s",
        "sleep_day=%s",
        "sleep_minutes=%s",
        "source_record_id=%s",
        "report_id=%s",
        "report_type=%s",
        "kind=%s",
        "is_incomplete=%s",
        "reason=%s",
        "LOG.exception(",
    )
    for field in prohibited_log_fields:
        assert field not in sources
