from __future__ import annotations

import json
from types import SimpleNamespace

import xiaomi_health_sync.blood_glucose_cli as glucose_cli


def test_inspect_cgm_recent_command_is_read_only():
    args = glucose_cli.build_parser().parse_args([
        "inspect-cgm-recent",
        "--minutes",
        "120",
    ])
    assert args.command == "inspect-cgm-recent"
    assert args.minutes == 120
    assert not hasattr(args, "send")


def test_cgm_inspection_summary_contains_only_timestamps_and_counts():
    rows = [
        {
            "sid": "secret-watch-sid",
            "key": "blood_sugar",
            "time": 1_787_440_000,
            "value": json.dumps({"time": 1_787_440_001, "blood_sugar": 5.7}),
        },
        {
            "sid": "secret-watch-sid",
            "key": "blood_sugar",
            "time": 1_787_440_300,
            "value": {"time": 1_787_440_300, "blood_sugar": 6.2},
        },
    ]

    summary = glucose_cli.summarize_cgm_rows(rows)

    assert summary == {
        "count": 2,
        "outer_timestamps": [1_787_440_000, 1_787_440_300],
        "inner_timestamps": [1_787_440_001, 1_787_440_300],
        "same_timestamp_count": 1,
        "has_glucose_field_count": 2,
    }
    serialized = json.dumps(summary)
    assert "secret-watch-sid" not in serialized
    assert "5.7" not in serialized
    assert "6.2" not in serialized


def test_record_cgm_inspection_uses_success_status_and_json_detail(monkeypatch):
    calls = {}

    class FakeStore:
        def __init__(self, *, url, service_role_key):
            calls["init"] = (url, service_role_key)

        def _update_sync_state(self, key, **fields):
            calls["key"] = key
            calls["fields"] = fields
            return key

    monkeypatch.setattr(glucose_cli, "DirectSupabaseStore", FakeStore)
    settings = SimpleNamespace(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="sb_secret_test",
    )
    summary = {
        "count": 2,
        "outer_timestamps": [1_787_440_000, 1_787_440_300],
        "inner_timestamps": [1_787_440_000, 1_787_440_300],
        "same_timestamp_count": 2,
        "has_glucose_field_count": 2,
    }

    glucose_cli.record_cgm_inspection(settings, summary)

    assert calls["key"] == "blood_sugar"
    assert calls["fields"]["source_check_status"] == "success"
    assert calls["fields"]["source_check_detail"] == {
        "kind": "cgm_readonly_inspect_v1",
        **summary,
    }
    assert calls["fields"]["empty_check_count"] == 0
    assert calls["fields"]["source_latest_at"].startswith("2026-")


def test_inspect_cgm_recent_reads_xiaomi_and_records_redacted_state(monkeypatch, capsys):
    class DummyClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    settings = object()
    calls = {}
    rows = [
        {
            "sid": "secret-watch-sid",
            "key": "blood_sugar",
            "time": 1_787_440_000,
            "value": {"time": 1_787_440_001, "blood_sugar": 5.7},
        }
    ]

    monkeypatch.setattr(glucose_cli, "load_settings", lambda: settings)
    monkeypatch.setattr(glucose_cli, "_open_client", lambda: DummyClient())

    def fake_read(**kwargs):
        calls["read"] = kwargs
        return rows

    def fake_record(loaded_settings, summary):
        calls["record"] = (loaded_settings, summary)

    monkeypatch.setattr(glucose_cli, "read_continuous_blood_glucose", fake_read)
    monkeypatch.setattr(glucose_cli, "record_cgm_inspection", fake_record)

    args = glucose_cli.build_parser().parse_args([
        "inspect-cgm-recent",
        "--minutes",
        "120",
    ])
    assert glucose_cli.run(args) == 0

    assert calls["record"][0] is settings
    assert calls["record"][1]["count"] == 1
    assert calls["read"]["end_time"] - calls["read"]["start_time"] == 120 * 60

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["status"] == "ok"
    assert payload["key"] == "blood_sugar"
    assert payload["count"] == 1
    assert payload["outer_timestamps"] == [1_787_440_000]
    assert payload["inner_timestamps"] == [1_787_440_001]
    assert "secret-watch-sid" not in output
    assert "5.7" not in output


def test_inspect_cgm_recent_reports_read_phase_without_leaking_details(monkeypatch, capsys):
    monkeypatch.setattr(glucose_cli, "load_settings", lambda: object())
    monkeypatch.setattr(
        glucose_cli,
        "_open_client",
        lambda: (_ for _ in ()).throw(RuntimeError("secret read failure details")),
    )

    args = glucose_cli.build_parser().parse_args(["inspect-cgm-recent", "--minutes", "120"])
    assert glucose_cli.run(args) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "Xiaomi CGM inspection failed: phase=read"
    assert "secret read failure details" not in captured.err


def test_inspect_cgm_recent_reports_record_phase_without_leaking_details(monkeypatch, capsys):
    class DummyClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(glucose_cli, "load_settings", lambda: object())
    monkeypatch.setattr(glucose_cli, "_open_client", lambda: DummyClient())
    monkeypatch.setattr(glucose_cli, "read_continuous_blood_glucose", lambda **kwargs: [])
    monkeypatch.setattr(
        glucose_cli,
        "record_cgm_inspection",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("secret record failure details")),
    )

    args = glucose_cli.build_parser().parse_args(["inspect-cgm-recent", "--minutes", "120"])
    assert glucose_cli.run(args) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "Xiaomi CGM inspection failed: phase=record"
    assert "secret record failure details" not in captured.err
