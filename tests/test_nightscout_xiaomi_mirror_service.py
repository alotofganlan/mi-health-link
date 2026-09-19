from pathlib import Path


def test_nightscout_runner_has_no_retired_mirror_switch():
    runner = Path("deploy/mi-health-link-nightscout-sync-runner.sh").read_text(encoding="utf-8")

    assert "XIAOMI_GLUCOSE_MIRROR_ENABLED" not in runner
    assert '"$APP_DIR/.venv/bin/mi-health-link-nightscout-sync"' in runner
