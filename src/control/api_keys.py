from __future__ import annotations

import hashlib
import hmac
import secrets


class ApiKeyService:
    def __init__(self, secret: str):
        normalized_secret = secret.strip()
        if not normalized_secret:
            raise ValueError("secret is required")
        self.secret = normalized_secret.encode("utf-8")

    def generate_key(self, prefix: str = "wk") -> str:
        normalized_prefix = prefix.strip()
        if not normalized_prefix:
            raise ValueError("prefix is required")
        if "_" in normalized_prefix:
            raise ValueError("prefix must not contain '_' characters")
        token = secrets.token_urlsafe(24)
        fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]
        return f"{normalized_prefix}_{fingerprint}_{token}"

    def key_id(self, key: str) -> str:
        parts = key.split("_", 2)
        if len(parts) < 3 or not parts[1]:
            raise ValueError("Malformed API key.")
        return parts[1]

    def hash_key(self, key: str) -> str:
        digest = hmac.new(self.secret, key.encode("utf-8"), hashlib.sha256).hexdigest()
        return digest

    def verify_key(self, key: str, hashed: str) -> bool:
        expected = self.hash_key(key)
        return hmac.compare_digest(expected, hashed)
