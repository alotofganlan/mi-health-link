from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_public_runtime_has_no_gmail_trigger() -> None:
    assert not (ROOT / "gmail_morning_report.py").exists()
    assert not (ROOT / "src/mi_health_link/gmail_morning_report.py").exists()

    public_configuration = "\n".join(
        (ROOT / name).read_text(encoding="utf-8")
        for name in (
            ".env.example",
            "README.md",
            "DEPLOYMENT.md",
            "PRIVACY.md",
            "PROJECT_STATUS.md",
            "docs/SLACK_SETUP.md",
            "pyproject.toml",
        )
    )
    assert "GMAIL_" not in public_configuration
    assert "Gmail" not in public_configuration


def test_readme_explains_nightscout_cgm_pipeline() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for expected in (
        "欧态动态血糖仪",
        "Nightscout",
        "Supabase",
        "每 5 分钟",
        "blood_sugar",
        "写回小米云",
    ):
        assert expected in readme


def test_slack_setup_documents_the_production_trigger_contract() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    guide = (ROOT / "docs/SLACK_SETUP.md").read_text(encoding="utf-8")

    assert "docs/SLACK_SETUP.md" in readme
    for expected in (
        "User Token Scopes",
        "chat:write",
        "SLACK_USER_TOKEN",
        "SLACK_CHANNEL_ID",
        "Xiaomi Health morning report ready",
        "get_morning_context(report_id)",
        "data_coverage",
    ):
        assert expected in guide
