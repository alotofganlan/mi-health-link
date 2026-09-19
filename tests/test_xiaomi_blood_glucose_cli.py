from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx

import mi_health_link.blood_glucose_cli as glucose_cli
from mi_health_link.blood_glucose import (
    XiaomiGlucoseBatchWriteResult,
)
from mi_health_link.config import Settings


def test_xiaomi_blood_glucose_cli_module_exists():
    assert importlib.util.find_spec("mi_health_link.blood_glucose_cli") is not None


def test_push_command_is_dry_run_unless_send_is_explicit():
    build_parser = getattr(glucose_cli, "build_parser", None)
    assert callable(build_parser)
    parser = build_parser()
    dry = parser.parse_args(["push", "--value", "5.6", "--timestamp", "1777000000"])
    live = parser.parse_args(["push", "--value", "5.6", "--timestamp", "1777000000", "--send"])
    assert dry.command == "push"
    assert dry.send is False
    assert dry.measurement_period == 2
    assert dry.zone_offset_seconds == 28800
    assert live.send is True


def test_read_command_is_separate_from_push():
    build_parser = getattr(glucose_cli, "build_parser", None)
    assert callable(build_parser)
    args = build_parser().parse_args(["read", "--start-time", "1776990000", "--end-time", "1777000000"])
    assert args.command == "read"
    assert args.start_time == 1776990000
    assert args.end_time == 1777000000


def test_probe_cgm_latest_two_command_exists_and_is_explicit_send():
    build_parser = getattr(glucose_cli, "build_parser", None)
    assert callable(build_parser)
    parser = build_parser()
    dry = parser.parse_args(["probe-cgm-latest-two"])
    live = parser.parse_args(["probe-cgm-latest-two", "--send"])
    assert dry.command == "probe-cgm-latest-two"
    assert dry.send is False
    assert live.send is True


def _settings() -> Settings:
    return Settings(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="sb_secret_test",
        credentials_file=Path("xiaomi-credentials.json"),
        region="cn",
        user_agent="test-agent",
    )


def test_latest_two_nightscout_loader_returns_two_samples_oldest_first():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/rest/v1/glucose_samples"
        assert request.url.params["source"] == "eq.nightscout"
        assert request.url.params["order"] == "measured_at.desc"
        assert request.url.params["limit"] == "2"
        return httpx.Response(200, json=[
            {
                "measured_at": "2026-08-22T12:49:05.771+00:00",
                "glucose_mmol_l": 5.4,
            },
            {
                "measured_at": "2026-08-22T12:44:05.771+00:00",
                "glucose_mmol_l": 5.1,
            },
        ])

    samples = glucose_cli.load_latest_nightscout_samples(
        _settings(),
        count=2,
        transport=httpx.MockTransport(handler),
    )
    assert [sample.timestamp for sample in samples] == [1787402645, 1787402945]
    assert [sample.glucose_mmol_l for sample in samples] == [5.1, 5.4]


def test_current_wearable_sid_uses_latest_xiaomi_resting_heart_rate_source():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/rest/v1/health_records"
        assert request.url.params["select"] == "sid"
        assert request.url.params["source"] == "eq.xiaomi"
        assert request.url.params["key"] == "eq.resting_heart_rate"
        assert request.url.params["sid"] == "not.is.null"
        assert request.url.params["order"] == "measured_at.desc"
        assert request.url.params["limit"] == "1"
        return httpx.Response(200, json=[{"sid": "watch-secret-sid"}])

    assert glucose_cli.load_current_wearable_sid(
        _settings(),
        transport=httpx.MockTransport(handler),
    ) == "watch-secret-sid"


def test_probe_cgm_latest_two_writes_batch_and_redacts_sensitive_details(monkeypatch, capsys):
    class DummyClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    settings = object()
    calls = {}
    samples = [
        glucose_cli.LatestNightscoutSample(timestamp=1787402645, glucose_mmol_l=5.1),
        glucose_cli.LatestNightscoutSample(timestamp=1787402945, glucose_mmol_l=5.4),
    ]
    monkeypatch.setattr(glucose_cli, "load_settings", lambda: settings)
    monkeypatch.setattr(
        glucose_cli,
        "load_latest_nightscout_samples",
        lambda loaded_settings, count: samples,
    )
    monkeypatch.setattr(
        glucose_cli,
        "load_current_wearable_sid",
        lambda loaded_settings: "watch-secret-sid",
    )
    monkeypatch.setattr(glucose_cli, "_open_client", lambda: DummyClient())

    def fake_write(**kwargs):
        calls.update(kwargs)
        return XiaomiGlucoseBatchWriteResult(
            uploaded_count=2,
            verified_count=2,
            already_present_count=0,
            timestamps=(1787402645, 1787402945),
        )

    monkeypatch.setattr(glucose_cli, "write_continuous_blood_glucose_batch", fake_write)
    args = glucose_cli.build_parser().parse_args(["probe-cgm-latest-two", "--send"])

    assert glucose_cli.run(args) == 0
    assert calls["sid"] == "watch-secret-sid"
    assert calls["phone_id"] == glucose_cli.MANUAL_RECORD_SID
    assert calls["samples"] == [
        (1787402645, 5.1),
        (1787402945, 5.4),
    ]

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output == {
        "status": "ok",
        "source": "nightscout",
        "requested_count": 2,
        "uploaded_count": 2,
        "verified_count": 2,
        "already_present_count": 0,
        "timestamps": [1787402645, 1787402945],
    }
    assert "watch-secret-sid" not in captured.out
    assert "5.1" not in captured.out
    assert "5.4" not in captured.out


def test_push_dry_run_prints_payload_without_loading_network(capsys):
    run = getattr(glucose_cli, "run", None)
    assert callable(run)
    args = glucose_cli.build_parser().parse_args([
        "push", "--value", "5.6", "--timestamp", "1777000000"
    ])

    assert run(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["sent"] is False
    assert output["path"] == "/app/v1/data/up_fitness_data"
    assert output["payload"]["data_list"][0]["key"] == "single_blood_sugar"
