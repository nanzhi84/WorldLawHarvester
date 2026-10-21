"""Private caller configuration and authenticated, opaque pagination cursors."""

import base64
import binascii
import hashlib
import hmac
import json
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from wlh.core import MAX_ORIGINAL_BYTES, WlhError, canonical


class ApiError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Caller(Strict):
    id: str = Field(min_length=1, max_length=100)
    token_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sources: list[str] = Field(max_length=10000)

    @field_validator("sources")
    @classmethod
    def source_ids(cls, values):
        import re

        if any(not re.fullmatch(r"dd-[0-9]{3,}", value) for value in values):
            raise ValueError("invalid_source_id")
        return values

    def authorize(self, source_id):
        if source_id not in self.sources:
            raise ApiError(403, "source_forbidden")


class Settings(Strict):
    public_base_url: str = Field(max_length=2000)
    cursor_secret: str = Field(min_length=32)
    callers: list[Caller] = Field(min_length=1, max_length=1000)
    workers: int = Field(default=4, ge=1, le=8)
    request_timeout_seconds: int = Field(default=30, ge=1, le=120)
    file_max_bytes: int = Field(default=134217728, ge=1024, le=MAX_ORIGINAL_BYTES)
    page_max_bytes: int = Field(default=67108864, ge=1024, le=134217728)

    @field_validator("public_base_url")
    @classmethod
    def base_url(cls, value):
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or any(ord(ch) < 33 or ord(ch) == 127 for ch in value)
        ):
            raise ValueError("invalid_public_base_url")
        _ = parts.port
        return value.rstrip("/")

    @model_validator(mode="after")
    def unique_callers(self):
        if len({c.id for c in self.callers}) != len(self.callers) or len(
            {c.token_sha256 for c in self.callers}
        ) != len(self.callers):
            raise ValueError("duplicate_caller")
        return self

    @classmethod
    def load(cls, path):
        try:
            import yaml

            from wlh.config import UniqueLoader

            return cls.model_validate(yaml.load(Path(path).read_text(), Loader=UniqueLoader))
        except (OSError, ValueError, TypeError):
            raise WlhError("invalid_api_config") from None

    def authenticate(self, authorization):
        scheme, _, key = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not key or len(key) > 4096 or key.isspace():
            raise ApiError(401, "unauthenticated")
        supplied = hashlib.sha256(key.encode()).hexdigest()
        caller = None
        for candidate in self.callers:
            if hmac.compare_digest(supplied, candidate.token_sha256):
                caller = candidate
        if caller is None:
            raise ApiError(401, "unauthenticated")
        return caller


class Cursor(Strict):
    projection: str = "template_16col"
    purpose: str = Field(default="results-cursor", pattern="^results-cursor$")
    caller: str
    source: str
    snapshot: str
    after: str = Field(min_length=1, max_length=1000)
    issued_at: int = Field(ge=0)


class Tokens:
    def __init__(self, settings):
        self.cipher = Fernet(
            base64.urlsafe_b64encode(hashlib.sha256(settings.cursor_secret.encode()).digest())
        )

    def encode(self, payload):
        return self.cipher.encrypt_at_time(
            canonical(payload.model_dump()), payload.issued_at
        ).decode()

    def decode(self, value, model=Cursor):
        status, code = 400, "invalid_cursor"
        try:
            if not value or len(value) > 4096:
                raise ValueError
            encoded = value.encode("ascii")
            if (
                base64.urlsafe_b64encode(base64.b64decode(encoded, altchars=b"-_", validate=True))
                != encoded
            ):
                raise ValueError
            raw = self.cipher.decrypt(encoded, ttl=3600)
            payload = model.model_validate(json.loads(raw))
            if payload.issued_at != self.cipher.extract_timestamp(value.encode("ascii")):
                raise ValueError
            return payload
        except (InvalidToken, UnicodeError, ValueError, ValidationError, binascii.Error):
            raise ApiError(status, code) from None
