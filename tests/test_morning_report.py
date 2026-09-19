from __future__ import annotations

from datetime import date
import importlib.util
import json
from pathlib import Path

import httpx
import pytest


REPORT_PATH = Path(__file__).parents[1] / "slack_morning_report.py"


def load_report_module():
    assert REPORT_PATH.is_file(), "the wake monitor report entry point is missing"
    spec = importlib.util.spec_from_file_location("slack_morning_report", REPORT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_entrypoint_exists_for_wake_monitor() -> None:
    assert REPORT_PATH.is_file()


def test_select_main_sleep_prefers_complete_watch_session() -> None:
    report = load_report_module()
    rows = [
        {
            "end_at": "2026-09-08T18:15:00+00:00",
            "raw": {
                "sleep_day": "2026-09-09",
                "sleep_source": "watch",
                "metrics": {"is_incomplete": True, "total_sleep_seconds": 118},
            },
        },
        {
            "end_at": "2026-09-08T22:51:00+00:00",
            "raw": {
                "sleep_day": "2026-09-09",
                "sleep_source": "watch",
                "metrics": {"is_incomplete": False, "total_sleep_seconds": 375},
            },
        },
    ]

    selected = report.select_main_sleep(rows, "2026-09-09")

    assert selected == rows[1]


def test_render_report_contains_sleep_and_available_health_data() -> None:
    report = load_report_module()
    sleep = {
        "start_at": "2026-09-08T16:17:00+00:00",
        "end_at": "2026-09-08T22:51:00+00:00",
        "avg_hr": 66,
        "avg_spo2": 98,
        "raw": {
            "metrics": {
                "total_sleep_seconds": 375,
                "deep_sleep_seconds": 112,
                "light_sleep_seconds": 161,
                "rem_sleep_seconds": 102,
                "awake_seconds": 19,
                "is_incomplete": False,
            }
        },
    }
    metrics = {
        "steps": [{"value": {"steps": 8123}}],
        "heart_rate": [{"bpm": 62}, {"bpm": 88}],
        "diet": [{"total_calorie_kcal": 640, "total_protein_g": 31.5}],
    }

    text = report.render_report(
        report_date=date(2026, 9, 9),
        sleep_day="2026-09-09",
        sleep=sleep,
        metrics=metrics,
    )

    assert "🌤 今日" in text
    assert "🌙 昨晚睡眠" in text
    assert "00:17" in text
    assert "06:51" in text
    assert "6小时15分" in text
    assert "深睡 1小时52分" in text
    assert "步数 8,123" in text
    assert "心率 62–88 bpm" in text
    assert "饮食 640 kcal" in text
    assert "昨日健康" in text


def test_post_slack_report_raises_for_http_failure() -> None:
    report = load_report_module()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(500, text="failed")

    with pytest.raises(httpx.HTTPStatusError):
        report.post_slack_report(
            "https://hooks.slack.test/services/test",
            "morning report",
            transport=httpx.MockTransport(handler),
        )

    assert len(seen) == 1
    assert json.loads(seen[0].content) == {"text": "morning report"}

def test_send_slack_report_prefers_user_token_over_webhook() -> None:
    report = load_report_module()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "ts": "123.456"})

    report.send_slack_report(
        "morning report",
        user_token="test-slack-user-token",
        channel_id="TEST_CHANNEL",
        webhook_url="https://hooks.slack.test/services/bot",
        transport=httpx.MockTransport(handler),
    )

    assert len(seen) == 1
    assert str(seen[0].url) == "https://slack.com/api/chat.postMessage"
    assert seen[0].headers["authorization"] == "Bearer test-slack-user-token"
    assert json.loads(seen[0].content) == {
        "channel": "TEST_CHANNEL",
        "text": "morning report",
    }


def test_send_slack_report_rejects_slack_api_error() -> None:
    report = load_report_module()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "not_in_channel"})

    with pytest.raises(ValueError, match="not_in_channel"):
        report.send_slack_report(
            "morning report",
            user_token="test-slack-user-token",
            channel_id="TEST_CHANNEL",
            webhook_url="",
            transport=httpx.MockTransport(handler),
        )


def test_send_slack_report_does_not_fallback_when_user_channel_is_missing() -> None:
    report = load_report_module()

    with pytest.raises(ValueError, match="SLACK_CHANNEL_ID"):
        report.send_slack_report(
            "morning report",
            user_token="test-slack-user-token",
            channel_id="",
            webhook_url="https://hooks.slack.test/services/bot",
        )


def test_render_trigger_does_not_require_slack_mention(monkeypatch) -> None:
    report = load_report_module()
    monkeypatch.delenv("SLACK_CHATGPT_MENTION", raising=False)
    assert hasattr(report, "render_trigger")
    text = report.render_trigger(date(2026, 9, 9))
    assert text.startswith("Xiaomi Health")
    assert "sleep_day=2026-09-09" in text
    assert "health_date=2026-09-08" in text


def test_render_trigger_uses_report_id_kind_and_mcp_context(monkeypatch) -> None:
    report = load_report_module()
    monkeypatch.setenv("WAKE_REPORT_ID", "report-123")
    monkeypatch.setenv("WAKE_REPORT_KIND", "morning")

    text = report.render_trigger(date(2026, 9, 15))

    assert text.startswith("Xiaomi Health morning report ready\n")
    assert "report_id=report-123" in text
    assert "report_kind=morning" in text
    assert "title=早安我的少年" in text
    assert "get_morning_context" in text
    assert "data_coverage" in text
    assert "health_date=" not in text
    assert "latitude" not in text
    assert "longitude" not in text


def test_render_trigger_labels_sleep_update(monkeypatch) -> None:
    report = load_report_module()
    monkeypatch.setenv("WAKE_REPORT_ID", "report-update")
    monkeypatch.setenv("WAKE_REPORT_KIND", "sleep_update")

    assert "title=睡眠更新" in report.render_trigger(date(2026, 9, 15))




def test_safe_error_text_redacts_slack_webhook(monkeypatch) -> None:
    report = load_report_module()
    webhook = "https://hooks.slack.test/services/test-path"
    monkeypatch.setenv("SLACK_WEBHOOK_URL", webhook)
    request = httpx.Request("POST", webhook)
    error = httpx.HTTPStatusError(
        f"500 for {webhook}",
        request=request,
        response=httpx.Response(500, request=request),
    )

    text = report.safe_error_text(error)

    assert webhook not in text
    assert "<redacted webhook>" in text


def test_safe_error_text_redacts_slack_user_token(monkeypatch) -> None:
    report = load_report_module()
    token = "test-slack-user-token"
    monkeypatch.setenv("SLACK_USER_TOKEN", token)

    text = report.safe_error_text(ValueError(f"request failed for {token}"))

    assert token not in text
    assert "<redacted Slack token>" in text
