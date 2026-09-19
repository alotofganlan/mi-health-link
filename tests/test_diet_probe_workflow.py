from pathlib import Path


WORKFLOW = (
    Path(__file__).parents[1]
    / ".github"
    / "workflows"
    / "diet-readonly-probe.yml"
)


def test_diet_probe_workflow_is_fixed_and_read_only():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "get_diet_records_by_time" in text
    for field in ("dining", "limit", "start_time", "end_time", "reverse", "next_key"):
        assert f'"{field}"' in text
    assert "Asia/Shanghai" in text
    assert "epoch-ms" in text
    assert "epoch-seconds" in text
    assert "full-range-ms" in text
    assert "dining-enum" not in text
    assert "encrypted_post" not in text
    assert "up_diet_records" not in text
    assert "delete_diet_records" not in text
    assert "save_raw" not in text
    assert "--save" not in text
    assert "workflow_dispatch" not in text
    assert "paths:" in text
