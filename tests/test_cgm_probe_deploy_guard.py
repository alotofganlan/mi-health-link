from pathlib import Path


def test_nightscout_service_runs_current_cgm_mirror():
    service = Path("deploy/mi-health-link-nightscout-sync.service").read_text(encoding="utf-8")
    runner_path = Path("deploy/mi-health-link-nightscout-sync-runner.sh")

    assert "ExecStart=/bin/bash %h/mi-health-link/deploy/mi-health-link-nightscout-sync-runner.sh" in service
    assert runner_path.exists()

    runner = runner_path.read_text(encoding="utf-8")
    assert "XIAOMI_GLUCOSE_MIRROR_ENABLED" not in runner
    assert '"$APP_DIR/.venv/bin/mi-health-link-nightscout-sync"' in runner

    # The periodic Xiaomi write path is the dedicated delayed-verification blood_sugar mirror.
    assert '"$APP_DIR/.venv/bin/python" -m mi_health_link.cgm_mirror' in runner

    # The timer path is limited to production sync commands.
    assert "inspect-cgm-recent" not in runner
    assert "mi-health-link-blood-glucose" not in runner
    assert "CGM_INSPECT_" not in runner
    assert "CGM_PROBE_" not in runner
    assert "probe-cgm-latest-two --send" not in runner
