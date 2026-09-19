from pathlib import Path


WORKFLOWS = Path(__file__).parents[1] / ".github" / "workflows"
PRIVATE_DEPLOYMENTS = (
    "deploy-vps.yml",
    "locate-diet-apk.yml",
)


def test_private_deployment_workflows_are_manual_only() -> None:
    for name in PRIVATE_DEPLOYMENTS:
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert "secrets.VPS_" in text, name
        assert "workflow_dispatch:" in text, name
        assert "\n  push:" not in text, name
