from __future__ import annotations

import base64
import hashlib
import os
import struct
import time
from dataclasses import dataclass
from typing import Callable


def b64d(value: str) -> bytes:
    return base64.b64decode(value)


def b64e(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def generate_nonce(now: float | None = None, random_bytes: bytes | None = None) -> str:
    """
    Xiaomi-style nonce:
      8 random bytes || big-endian floor(unix_seconds / 60)
    then Base64.
    """
    now = time.time() if now is None else now
    rnd = os.urandom(8) if random_bytes is None else random_bytes
    if len(rnd) != 8:
        raise ValueError("random_bytes must be exactly 8 bytes")
    minute = int(now // 60)
    return b64e(rnd + struct.pack(">I", minute))


def signed_nonce(ssecurity_b64: str, nonce_b64: str) -> str:
    digest = hashlib.sha256(b64d(ssecurity_b64) + b64d(nonce_b64)).digest()
    return b64e(digest)


class RC4:
    def __init__(self, key: bytes):
        if not key:
            raise ValueError("RC4 key must not be empty")
        s = list(range(256))
        j = 0
        for i in range(256):
            j = (j + s[i] + key[i % len(key)]) & 0xFF
            s[i], s[j] = s[j], s[i]
        self._s = s
        self._i = 0
        self._j = 0

    def crypt(self, data: bytes) -> bytes:
        out = bytearray()
        s = self._s
        i, j = self._i, self._j
        for byte in data:
            i = (i + 1) & 0xFF
            j = (j + s[i]) & 0xFF
            s[i], s[j] = s[j], s[i]
            k = s[(s[i] + s[j]) & 0xFF]
            out.append(byte ^ k)
        self._i, self._j = i, j
        return bytes(out)


def rc4_xiaomi(key_b64: str, data: bytes) -> bytes:
    """
    Xiaomi Health transport uses RC4 with a 1024-byte keystream discard.
    """
    cipher = RC4(b64d(key_b64))
    cipher.crypt(b"\x00" * 1024)
    return cipher.crypt(data)


def sha1_b64(text: str) -> str:
    return b64e(hashlib.sha1(text.encode("utf-8")).digest())


@dataclass(frozen=True)
class SignedPayload:
    nonce: str
    signed_nonce: str
    data: str
    rc4_hash__: str
    signature: str


def sign_payload(
    *,
    path: str,
    plaintext: str,
    ssecurity_b64: str,
    nonce: str,
) -> SignedPayload:
    sn = signed_nonce(ssecurity_b64, nonce)

    # Pre-encryption hash uses plaintext data.
    rc4_hash_material = f"POST&{path}&data={plaintext}&{sn}"
    rc4_hash = b64e(rc4_xiaomi(sn, hashlib.sha1(rc4_hash_material.encode()).digest()))

    encrypted_data = b64e(rc4_xiaomi(sn, plaintext.encode("utf-8")))

    signature_material = (
        f"POST&{path}&data={encrypted_data}&rc4_hash__={rc4_hash}&{sn}"
    )
    signature = sha1_b64(signature_material)

    return SignedPayload(
        nonce=nonce,
        signed_nonce=sn,
        data=encrypted_data,
        rc4_hash__=rc4_hash,
        signature=signature,
    )


def decrypt_response(*, ciphertext_b64: str, signed_nonce_b64: str) -> str:
    clear = rc4_xiaomi(signed_nonce_b64, b64d(ciphertext_b64))
    return clear.decode("utf-8")
