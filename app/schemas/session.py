"""Browser session DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class BrowserSessionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    domain: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description=(
            "Bare hostname the cookies apply to, e.g. 'oatd.org'. "
            "Cookies will only be injected for requests to this domain."
        ),
    )
    # Accepts either a JSON array of Playwright cookie objects, or a
    # semicolon/newline-delimited name=value string from browser dev tools.
    cookies_raw: str = Field(
        ...,
        description=(
            "Cookie data as a JSON array of Playwright cookie objects "
            "([{name, value, domain, path, ...}]) or a simple "
            "'name=value; name2=value2' string."
        ),
    )
    user_agent: str | None = Field(default=None, max_length=512)
    expires_at: datetime | None = None

    @field_validator("cookies_raw")
    @classmethod
    def validate_cookies_raw(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("cookies_raw must not be empty")
        return v


class BrowserSessionResponse(BaseModel):
    id: int
    name: str
    domain: str
    user_agent: str | None = None
    expires_at: datetime | None = None
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


def parse_cookies_raw(raw: str) -> list[dict[str, Any]]:
    """Parse user-supplied cookie data into a list of Playwright cookie dicts.

    Accepts:
    - JSON array: [{"name": "x", "value": "y", "domain": "...", ...}]
    - Simple string: "name=value; name2=value2"
    """
    import json

    raw = raw.strip()
    if raw.startswith("["):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON cookie array: {exc}") from exc
        if not isinstance(data, list):
            raise ValueError("cookies_raw JSON must be an array")
        for item in data:
            if not isinstance(item, dict):
                raise ValueError("Each cookie must be a JSON object")
            if "name" not in item or "value" not in item:
                raise ValueError(
                    "Each cookie object must have 'name' and 'value' fields"
                )
        return data

    # Simple name=value pairs (semicolon or newline delimited).
    cookies: list[dict[str, Any]] = []
    for part in raw.replace("\n", ";").split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        cookies.append({"name": name.strip(), "value": value.strip()})
    if not cookies:
        raise ValueError(
            "No valid cookies found. Provide JSON or 'name=value; ...' format."
        )
    return cookies
