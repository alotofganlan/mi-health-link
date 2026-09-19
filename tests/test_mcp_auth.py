from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from xiaomi_health_sync.mcp_auth import SupabaseTokenVerifier
from xiaomi_health_sync.mcp_config import MCPSettings


ISSUER = "https://project.supabase.co/auth/v1"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
KID = "test-key"
SUBJECT = "user-123"
AUDIENCE = "authenticated"


def _settings() -> MCPSettings:
    return MCPSettings(
        public_url="https://health.example.com/mcp",
        host="127.0.0.1",
        port=8765,
        supabase_issuer_url=ISSUER,
        supabase_jwks_url=JWKS_URL,
        allowed_subject=SUBJECT,
        required_scopes=("openid",),
        supabase_project_url="https://project.supabase.co",
        supabase_publishable_key="sb_publishable_example",
        supabase_audience=AUDIENCE,
    )


@pytest.fixture
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks(private_key, *, kid: str = KID) -> dict:
    public_key = private_key.public_key()
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(public_key))
    jwk["kid"] = kid
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    return {"keys": [jwk]}


def _token(
    private_key,
    *,
    subject: str = SUBJECT,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    scope: str = "openid",
    expires_delta: timedelta = timedelta(minutes=10),
    kid: str = KID,
) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": subject,
            "iss": issuer,
            "aud": audience,
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int((now + expires_delta).timestamp()),
            "scope": scope,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": kid},
    )


def _es256_jwks(private_key, *, kid: str = KID) -> dict:
    public_key = private_key.public_key()
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(public_key))
    jwk["kid"] = kid
    jwk["use"] = "sig"
    jwk["alg"] = "ES256"
    return {"keys": [jwk]}


def _es256_token(private_key, *, kid: str = KID) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": SUBJECT,
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=10)).timestamp()),
            "scope": "openid",
        },
        private_key,
        algorithm="ES256",
        headers={"kid": kid},
    )


def _transport(jwks: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == JWKS_URL:
            return httpx.Response(200, json=jwks)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_accepts_valid_token_for_allowed_subject(signing_key) -> None:
    verifier = SupabaseTokenVerifier(_settings(), transport=_transport(_jwks(signing_key)))

    access_token = asyncio.run(verifier.verify_token(_token(signing_key)))

    assert access_token is not None
    assert access_token.client_id == SUBJECT
    assert access_token.scopes == ["openid"]
    assert access_token.subject == SUBJECT


def test_accepts_valid_es256_token_for_allowed_subject() -> None:
    signing_key = ec.generate_private_key(ec.SECP256R1())
    verifier = SupabaseTokenVerifier(
        _settings(),
        transport=_transport(_es256_jwks(signing_key)),
    )

    access_token = asyncio.run(verifier.verify_token(_es256_token(signing_key)))

    assert access_token is not None
    assert access_token.subject == SUBJECT
    assert access_token.scopes == ["openid"]


def test_rejects_valid_token_for_other_subject(signing_key) -> None:
    verifier = SupabaseTokenVerifier(_settings(), transport=_transport(_jwks(signing_key)))

    access_token = asyncio.run(
        verifier.verify_token(_token(signing_key, subject="other-user"))
    )

    assert access_token is None


def test_rejects_expired_token(signing_key) -> None:
    verifier = SupabaseTokenVerifier(_settings(), transport=_transport(_jwks(signing_key)))

    access_token = asyncio.run(
        verifier.verify_token(_token(signing_key, expires_delta=timedelta(minutes=-1)))
    )

    assert access_token is None


def test_rejects_wrong_issuer(signing_key) -> None:
    verifier = SupabaseTokenVerifier(_settings(), transport=_transport(_jwks(signing_key)))

    access_token = asyncio.run(
        verifier.verify_token(_token(signing_key, issuer="https://other.example/auth/v1"))
    )

    assert access_token is None


def test_rejects_wrong_audience(signing_key) -> None:
    verifier = SupabaseTokenVerifier(_settings(), transport=_transport(_jwks(signing_key)))

    access_token = asyncio.run(
        verifier.verify_token(_token(signing_key, audience="some-other-api"))
    )

    assert access_token is None


def test_rejects_missing_required_scope(signing_key) -> None:
    verifier = SupabaseTokenVerifier(_settings(), transport=_transport(_jwks(signing_key)))

    access_token = asyncio.run(verifier.verify_token(_token(signing_key, scope="profile")))

    assert access_token is None


def test_rejects_unknown_kid_after_one_jwks_refresh(signing_key) -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        if str(request.url) == JWKS_URL:
            requests += 1
            return httpx.Response(200, json=_jwks(signing_key))
        return httpx.Response(404)

    verifier = SupabaseTokenVerifier(
        _settings(),
        transport=httpx.MockTransport(handler),
    )

    access_token = asyncio.run(
        verifier.verify_token(_token(signing_key, kid="unknown-key"))
    )

    assert access_token is None
    assert requests == 2
