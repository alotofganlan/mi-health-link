from pathlib import Path


WORKFLOW = (
    Path(__file__).parents[1]
    / ".github"
    / "workflows"
    / "inspect-official-apk-url.yml"
)


def test_official_apk_url_inspector_is_read_only_and_host_limited():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert '"watch.iot.mi.com",' in text
    assert '"cdn.web-global.fds.api.mi-img.com",' in text
    assert '"hlth.io.mi.com",' in text
    assert '"region.hlth.io.mi.com",' in text
    assert 'parsed.scheme != "https"' in text
    assert "parsed.hostname not in ALLOWED_HOSTS" in text
    assert "urlopen(request" in text
    assert "class NoRedirect" in text
    assert '"https://hlth.io.mi.com/download?appId=mifitness"' in text
    assert '"https://region.hlth.io.mi.com/abroad_download?appId=mifitness"' in text
    assert 'Request(endpoint, method="GET"' in text
    assert "except HTTPError as exc:" in text
    assert "except Exception as exc:" in text
    assert "MAX_BYTES = 20_000_000" in text
    assert "subprocess" not in text
    assert "os.system" not in text
    assert "pip install" not in text
    assert "curl " not in text
