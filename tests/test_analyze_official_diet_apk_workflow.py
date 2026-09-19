from pathlib import Path


WORKFLOW = (
    Path(__file__).parents[1]
    / ".github"
    / "workflows"
    / "analyze-official-diet-apk.yml"
)


def test_official_diet_apk_analysis_is_bounded_and_static_only():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert '"https://hlth.io.mi.com/download?appId=mifitness"' in text
    assert "class NoRedirect" in text
    assert "MAX_APK_BYTES" in text
    assert 'hostname.endswith(".fds.api.mi-img.com")' in text
    assert "hashlib.sha256" in text
    assert r'b"PK\x03\x04"' in text
    assert r'b"PK\\x03\\x04"' not in text
    assert "zipfile.ZipFile" in text
    assert '"classes10.dex"' in text
    assert "dexdump" in text
    assert "apkanalyzer" in text
    assert '"${APK_ANALYZER}" apk summary "${APK_PATH}" || true' in text
    assert '"${APK_ANALYZER}" dex list "${APK_PATH}" || true' in text
    assert "get_diet_records_by_time" in text
    assert "DietRecordsRequestParams" in text
    assert '"Lcom/xiaomi/fitness/health/heat/data/DietRecordsRequestParams;"' in text
    assert '"data/get_diet_records_by_time"' in text
    assert 'SECTION_MARKER = "Class #"' in text
    assert "MAX_SECTION_LINES = 2500" in text
    assert "up_diet_records" not in text
    assert "delete_diet_records" not in text
    assert "adb install" not in text
    assert "pm install" not in text
    assert "pip install" not in text
    assert "chmod +x" not in text
