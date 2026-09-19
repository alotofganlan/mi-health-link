from pathlib import Path


def test_primary_deploy_disables_removed_polling_service_during_upgrade():
    primary = Path(".github/workflows/deploy-vps.yml").read_text(encoding="utf-8")
    assert "xiaomi-health-auto-sync.timer" in primary
    assert "import xiaomi_health_sync.wake_probe" not in primary
    assert "disable --now xiaomi-health-wake-monitor.service" in primary


def test_removed_polling_runtime_files_stay_removed():
    for path in (
        "src/xiaomi_health_sync/wake_probe.py",
        "src/xiaomi_health_sync/wake_monitor.py",
        "src/xiaomi_health_sync/wake_detector.py",
        "deploy/xiaomi-health-wake-monitor.service",
        ".github/workflows/deploy-wake-monitor.yml",
    ):
        assert not Path(path).exists(), path
