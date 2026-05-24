"""Authentication service with password hashing and token management."""

import hashlib
import secrets

class AuthService:
    def __init__(self):
        self._tokens: dict[str, str] = {}

    def hash_password(self, password: str) -> str:
        salt = secrets.token_hex(16)
        hashed = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
        return f"{salt}:{hashed}"

    def verify_password(self, password: str, stored: str) -> bool:
        salt, hashed = stored.split(":")
        return hashlib.sha256(f"{salt}{password}".encode()).hexdigest() == hashed

    def create_token(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        self._tokens[token] = user_id
        return token

    def validate_token(self, token: str) -> str | None:
        return self._tokens.get(token)

    def revoke_token(self, token: str) -> bool:
        return self._tokens.pop(token, None) is not None
