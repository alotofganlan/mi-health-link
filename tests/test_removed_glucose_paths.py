from pathlib import Path

import pytest

from mi_health_link import blood_glucose_cli


def test_legacy_nightscout_single_point_command_is_not_available() -> None:
    with pytest.raises(SystemExit):
        blood_glucose_cli.build_parser().parse_args(["push-latest-nightscout"])


def test_nightscout_ingestion_has_no_retired_mirror_switch() -> None:
    source = Path("src/mi_health_link/nightscout_sync.py").read_text(
        encoding="utf-8"
    )
    assert "XIAOMI_GLUCOSE_MIRROR_ENABLED" not in source
    assert "mirror_nightscout_to_xiaomi" not in source
    assert "mirror_continuous_nightscout_to_xiaomi" not in source


def test_runner_uses_only_ingestion_and_current_cgm_mirror() -> None:
    runner = Path("deploy/mi-health-link-nightscout-sync-runner.sh").read_text(
        encoding="utf-8"
    )
    assert "XIAOMI_GLUCOSE_MIRROR_ENABLED" not in runner
    assert '"$APP_DIR/.venv/bin/mi-health-link-nightscout-sync"' in runner
    assert '"$APP_DIR/.venv/bin/python" -m mi_health_link.cgm_mirror' in runner
