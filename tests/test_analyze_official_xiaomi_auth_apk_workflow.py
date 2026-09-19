from pathlib import Path


WORKFLOW = (
    Path(__file__).parents[1]
    / ".github"
    / "workflows"
    / "analyze-official-xiaomi-auth-apk.yml"
)


def test_xiaomi_auth_apk_analysis_is_static_and_secret_free():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert '"https://hlth.io.mi.com/download?appId=mifitness"' in text
    assert "MAX_APK_BYTES" in text
    assert "zipfile.ZipFile" in text
    assert 'name.startswith("classes") and name.endswith(".dex")' in text
    for needle in (
        "serviceToken",
        "ssecurity",
        "passToken",
        "refreshToken",
        "refresh_token",
        "serviceLoginAuth2",
        "miothealth",
        r"account\.xiaomi\.com",
        "clientSign",
    ):
        assert needle in text
    assert "dexdump" in text
    assert "getServiceToken url forceRefresh" in text
    assert "classes2.dex" in text
    assert "invalidateServiceToken" in text
    assert "processUnAuthorized.refreshToken" in text
    assert "MAX_TARGET_SECTIONS = 30" in text
    assert "secrets." not in text
    assert "VPS_" not in text
    assert "adb install" not in text
    assert "pm install" not in text
    assert "pip install" not in text
    assert "chmod +x" not in text
