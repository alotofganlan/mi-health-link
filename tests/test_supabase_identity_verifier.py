from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from mi_health_link.mcp_auth import SupabaseTokenVerifier
from mi_health_link.mcp_config import MCPSettings


ISSUER = "https://project.supabase.co/auth/v1"
JWKS = f"{ISSUER}/.well-known/jwks.json"


def test_identity_verifier_accepts_normal_supabase_session_without_oauth_scope() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": "identity-key", "alg": "RS256", "use": "sig"})

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == JWKS:
            return httpx.Response(200, json={"keys": [jwk]})
        return httpx.Response(404)

    settings = MCPSettings(
        public_url="https://health.example.com/mcp",
        host="127.0.0.1",
        port=8765,
        supabase_issuer_url=ISSUER,
        supabase_jwks_url=JWKS,
        allowed_subject="user-123",
        required_scopes=("openid",),
        supabase_project_url="https://project.supabase.co",
        supabase_publishable_key="sb_publishable_example",
        supabase_audience="authenticated",
    )
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "user-123",
            "iss": ISSUER,
            "aud": "authenticated",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=10)).timestamp()),
            "role": "authenticated",
        },
        key,
        algorithm="RS256",
        headers={"kid": "identity-key"},
    )

    verifier = SupabaseTokenVerifier(
        settings,
        transport=httpx.MockTransport(handler),
        required_scopes=(),
    )
    verified = asyncio.run(verifier.verify_token(token))

    assert verified is not None
    assert verified.subject == "user-123"
    assert verified.scopes == []
