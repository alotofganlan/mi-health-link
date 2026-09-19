from pathlib import Path


WORKFLOW = (
    Path(__file__).parents[1]
    / ".github"
    / "workflows"
    / "locate-diet-apk.yml"
)


def test_apk_locator_is_read_only_and_bounded():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert '{ find "$HOME" /tmp -maxdepth 7 -type f' in text
    assert "|| true; } | head -200" in text
    assert "-delete" not in text
    assert " rm " not in text
    assert "-exec" not in text
    assert "base.apk" in text
    assert "classes10.dex" in text
