import base64
import hashlib
import json

from xiaomi_health_sync.auth import (
    append_query,
    make_client_sign,
    parse_xiaomi_json,
)


def test_parse_xiaomi_prefixed_json():
    assert parse_xiaomi_json('&&&START&&&{"code":0}') == {"code": 0}


def test_client_sign_matches_sha1_definition():
    expected = base64.b64encode(
        hashlib.sha1(b"nonce=123&abc").digest()
    ).decode("ascii")
    assert make_client_sign(123, "abc") == expected


def test_append_query_preserves_existing_query():
    url = append_query("https://example.test/sts?a=1", clientSign="xyz")
    assert "a=1" in url
    assert "clientSign=xyz" in url
