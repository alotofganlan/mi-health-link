from pathlib import Path


def test_primary_deploy_disables_removed_polling_service_during_upgrade():
    primary = Path(".github/workflows/deploy-vps.yml").read_text(encoding="utf-8")
    assert "mi-health-link-auto-sync.timer" in primary
    assert "import mi_health_link.wake_probe" not in primary
    assert "disable --now mi-health-link-wake-monitor.service" in primary


def test_removed_polling_runtime_files_stay_removed():
    for path in (
        "src/mi_health_link/wake_probe.py",
        "src/mi_health_link/wake_monitor.py",
        "src/mi_health_link/wake_detector.py",
        "deploy/mi-health-link-wake-monitor.service",
        ".github/workflows/deploy-wake-monitor.yml",
    ):
        assert not Path(path).exists(), path
