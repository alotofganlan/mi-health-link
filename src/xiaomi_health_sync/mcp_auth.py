from __future__ import annotations

import time
from typing import Any

import httpx
import jwt
from jwt.algorithms import ECAlgorithm, RSAAlgorithm
from mcp.server.auth.provider import AccessToken, TokenVerifier

from .mcp_config import MCPSettings


JWKS_CACHE_SECONDS = 300
SUPPORTED_JWT_ALGORITHMS = {"RS256", "ES256"}


class CompositeTokenVerifier(TokenVerifier):
    """Try the new VPS verifier first, then an optional legacy verifier."""

    def __init__(self, primary: TokenVerifier, legacy: TokenVerifier | None = None) -> None:
        self.primary = primary
        self.legacy = legacy

    async def verify_token(self, token: str) -> AccessToken | None:
        accepted = await self.primary.verify_token(token)
        if accepted is not None or self.legacy is None:
            return accepted
        return await self.legacy.verify_token(token)


class SupabaseTokenVerifier(TokenVerifier):
    def __init__(
        self,
        settings: MCPSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        required_scopes: tuple[str, ...] | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.required_scopes = settings.required_scopes if required_scopes is None else required_scopes
        self._keys: dict[str, tuple[str, Any]] = {}
        self._keys_loaded_at = 0.0

    @staticmethod
    def _verification_key(item: dict[str, Any]) -> tuple[str, Any] | None:
        alg = item.get("alg")
        kty = item.get("kty")
        if alg == "RS256" and kty == "RSA":
            return alg, RSAAlgorithm.from_jwk(item)
        if alg == "ES256" and kty == "EC":
            return alg, ECAlgorithm.from_jwk(item)
        return None

    async def _load_keys(self, *, force: bool = False) -> dict[str, tuple[str, Any]]:
        now = time.monotonic()
        if (
            not force
            and self._keys
            and now - self._keys_loaded_at < JWKS_CACHE_SECONDS
        ):
            return self._keys

        async with httpx.AsyncClient(timeout=10.0, transport=self.transport) as client:
            response = await client.get(self.settings.supabase_jwks_url)
            response.raise_for_status()
            payload = response.json()

        keys: dict[str, tuple[str, Any]] = {}
        for item in payload.get("keys", []):
            if not isinstance(item, dict):
                continue
            kid = item.get("kid")
            if not kid:
                continue
            try:
                verification_key = self._verification_key(item)
            except (TypeError, ValueError):
                continue
            if verification_key is not None:
                keys[str(kid)] = verification_key

        self._keys = keys
        self._keys_loaded_at = now
        return keys

    async def _key_for_token(self, token: str) -> tuple[str, Any] | None:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            return None
        kid = header.get("kid")
        header_alg = header.get("alg")
        if not kid or header_alg not in SUPPORTED_JWT_ALGORITHMS:
            return None

        try:
            keys = await self._load_keys()
            key_entry = keys.get(str(kid))
            if key_entry is None:
                keys = await self._load_keys(force=True)
                key_entry = keys.get(str(kid))
        except (httpx.HTTPError, ValueError, TypeError):
            return None

        if key_entry is None:
            return None
        jwk_alg, key = key_entry
        if header_alg != jwk_alg:
            return None
        return jwk_alg, key

    @staticmethod
    def _scopes(claims: dict[str, Any]) -> list[str]:
        raw = claims.get("scope")
        if isinstance(raw, str):
            return [scope for scope in raw.split() if scope]
        if isinstance(raw, list):
            return [str(scope) for scope in raw if str(scope)]
        raw = claims.get("scopes")
        if isinstance(raw, list):
            return [str(scope) for scope in raw if str(scope)]
        return []

    async def verify_token(self, token: str) -> AccessToken | None:
        key_entry = await self._key_for_token(token)
        if key_entry is None:
            return None
        algorithm, key = key_entry

        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=[algorithm],
                issuer=self.settings.supabase_issuer_url,
                audience=self.settings.supabase_audience,
                options={"require": ["sub", "iss", "aud", "exp"]},
            )
        except jwt.PyJWTError:
            return None

        subject = claims.get("sub")
        if not isinstance(subject, str) or subject != self.settings.allowed_subject:
            return None

        scopes = self._scopes(claims)
        if any(scope not in scopes for scope in self.required_scopes):
            return None

        client_id = claims.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            client_id = subject

        expires_at = claims.get("exp")
        if not isinstance(expires_at, int):
            expires_at = None

        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            expires_at=expires_at,
            subject=subject,
            claims={
                "iss": self.settings.supabase_issuer_url,
                "aud": self.settings.supabase_audience,
            },
        )
