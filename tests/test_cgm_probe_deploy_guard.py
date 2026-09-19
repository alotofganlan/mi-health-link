from pathlib import Path


def test_nightscout_service_runs_current_cgm_mirror():
    service = Path("deploy/xiaomi-health-nightscout-sync.service").read_text(encoding="utf-8")
    runner_path = Path("deploy/xiaomi-health-nightscout-sync-runner.sh")

    assert "ExecStart=/bin/bash %h/mi-health-link/deploy/xiaomi-health-nightscout-sync-runner.sh" in service
    assert runner_path.exists()

    runner = runner_path.read_text(encoding="utf-8")
    assert "XIAOMI_GLUCOSE_MIRROR_ENABLED" not in runner
    assert '"$APP_DIR/.venv/bin/xiaomi-health-nightscout-sync"' in runner

    # The periodic Xiaomi write path is the dedicated delayed-verification blood_sugar mirror.
    assert '"$APP_DIR/.venv/bin/python" -m xiaomi_health_sync.cgm_mirror' in runner

    # The timer path is limited to production sync commands.
    assert "inspect-cgm-recent" not in runner
    assert "xiaomi-health-blood-glucose" not in runner
    assert "CGM_INSPECT_" not in runner
    assert "CGM_PROBE_" not in runner
    assert "probe-cgm-latest-two --send" not in runner
