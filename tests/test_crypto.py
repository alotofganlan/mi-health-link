import base64

from mi_health_link.crypto import (
    RC4,
    decrypt_response,
    generate_nonce,
    rc4_xiaomi,
    signed_nonce,
)


def test_nonce_has_expected_structure():
    nonce = generate_nonce(
        now=120.0,
        random_bytes=b"12345678",
    )
    raw = base64.b64decode(nonce)
    assert raw[:8] == b"12345678"
    assert raw[8:] == b"\x00\x00\x00\x02"


def test_signed_nonce_is_deterministic():
    ssecurity = base64.b64encode(b"s" * 16).decode()
    nonce = base64.b64encode(b"n" * 12).decode()
    assert signed_nonce(ssecurity, nonce) == signed_nonce(ssecurity, nonce)


def test_xiaomi_rc4_roundtrip():
    key = base64.b64encode(b"test-key-material").decode()
    clear = b"hello xiaomi health"
    encrypted = rc4_xiaomi(key, clear)
    assert encrypted != clear
    decrypted = rc4_xiaomi(key, encrypted)
    assert decrypted == clear
