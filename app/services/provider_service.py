"""Provider configuration and BYOK LiteLLM service."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Sequence

from cryptography.fernet import Fernet
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.provider_config import ProviderConfig
from app.models.user import User
from app.schemas.provider import ProviderConfigCreate, ProviderConfigUpdate


logger = logging.getLogger(__name__)
PROVIDER_CONFIG_LOCK_NAMESPACE = 47005
REDACTED_SECRET = "[REDACTED_SECRET]"
_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(
        r"(?i)((?:api[_-]?key|x-api-key|authorization|password"
        r"|token|access_token|refresh_token|secret"
        r"|hashed_password|api_key_encrypted)"
        r"\s*[:=]\s*)[^\s,;]+"
    ),
    re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b"),
    re.compile(r"\bsk-proj-[A-Za-z0-9_-]{6,}\b"),
)


class ProviderServiceError(Exception):
    """Base provider service error."""


class ProviderJSONError(ProviderServiceError):
    """Raised when a provider cannot produce valid schema-conforming JSON."""

    def __init__(self, message: str, raw_response: str | None = None) -> None:
        super().__init__(message)
        self.raw_response = raw_response


class ProviderCallError(ProviderServiceError):
    """Raised when the provider call fails before parsing."""


class CapabilityProbeResponse(BaseModel):
    """Small response schema used to test structured JSON output."""

    ok: bool


@dataclass(frozen=True)
class JSONCallResult:
    """Parsed provider JSON response plus capability metadata."""

    data: BaseModel
    used_native_json: bool
    raw_response: str


def _fernet() -> Fernet:
    return Fernet(settings.PROVIDER_KEY_ENCRYPTION_SECRET.encode("utf-8"))


def encrypt_api_key(api_key: str) -> str:
    """Encrypt a provider API key for storage."""
    return _fernet().encrypt(api_key.encode("utf-8")).decode("utf-8")


def decrypt_api_key(api_key_encrypted: str) -> str:
    """Decrypt a provider API key for one provider call."""
    return _fernet().decrypt(api_key_encrypted.encode("utf-8")).decode("utf-8")


def redact_provider_secret(value: object, secrets: Sequence[str | None] = ()) -> str:
    """Remove known provider secrets from text before logging, prompting, or returning."""
    text = str(value)
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, REDACTED_SECRET)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(
            lambda match: (
                f"{match.group(1)}{REDACTED_SECRET}"
                if match.lastindex
                else REDACTED_SECRET
            ),
            text,
        )
    return text


def safe_provider_error_message(error: BaseException | str, api_key: str | None = None) -> str:
    """Return a useful provider error message without leaking key material."""
    raw = str(error) or (
        error.__class__.__name__ if isinstance(error, BaseException) else "Provider error"
    )
    redacted = redact_provider_secret(raw, [api_key])
    lower = redacted.lower()

    if any(token in lower for token in ("invalid api key", "incorrect api key", "unauthorized")):
        return "Authentication failed"
    if any(token in lower for token in ("authentication", "permission denied", "forbidden")):
        return "Authentication failed"
    if any(token in lower for token in ("rate limit", "429", "too many requests")):
        return "Rate limited"
    if any(token in lower for token in ("timeout", "timed out")):
        return "Provider timeout"
    if any(token in lower for token in ("json", "schema", "validation")):
        return "Invalid JSON response"

    return redacted


async def list_provider_configs(db: AsyncSession, user_id: int) -> Sequence[ProviderConfig]:
    """List provider configs owned by a user."""
    result = await db.execute(
        select(ProviderConfig)
        .where(ProviderConfig.user_id == user_id)
        .order_by(ProviderConfig.is_default.desc(), ProviderConfig.created_at.asc())
    )
    return result.scalars().all()


async def get_provider_config(
    db: AsyncSession,
    user_id: int,
    provider_config_id: int,
) -> ProviderConfig | None:
    """Fetch a provider config by id, scoped to the owning user."""
    result = await db.execute(
        select(ProviderConfig).where(
            ProviderConfig.id == provider_config_id,
            ProviderConfig.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def _user_has_provider_configs(db: AsyncSession, user_id: int) -> bool:
    result = await db.execute(
        select(func.count(ProviderConfig.id)).where(ProviderConfig.user_id == user_id)
    )
    return (result.scalar_one() or 0) > 0


async def _clear_default_provider(db: AsyncSession, user_id: int) -> None:
    await db.execute(
        update(ProviderConfig)
        .where(ProviderConfig.user_id == user_id, ProviderConfig.is_default.is_(True))
        .values(is_default=False)
    )
    user = await db.get(User, user_id)
    if user is not None:
        user.default_provider_id = None


async def _lock_user_provider_configs(db: AsyncSession, user_id: int) -> None:
    """Serialize provider config writes for one user."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:namespace, :user_id)"),
        {"namespace": PROVIDER_CONFIG_LOCK_NAMESPACE, "user_id": user_id},
    )


async def create_provider_config(
    db: AsyncSession,
    user_id: int,
    payload: ProviderConfigCreate,
) -> ProviderConfig:
    """Create a provider config and encrypt its API key."""
    await _lock_user_provider_configs(db, user_id)
    is_first_provider = not await _user_has_provider_configs(db, user_id)
    should_default = payload.is_default or is_first_provider

    if should_default:
        await _clear_default_provider(db, user_id)

    provider_config = ProviderConfig(
        user_id=user_id,
        name=payload.name,
        provider=payload.provider,
        model=payload.model,
        api_key_encrypted=encrypt_api_key(payload.api_key),
        is_default=should_default,
        capability_flags={},
    )
    db.add(provider_config)
    await db.flush()

    if should_default:
        user = await db.get(User, user_id)
        if user is not None:
            user.default_provider_id = provider_config.id

    await db.commit()
    await db.refresh(provider_config)
    logger.info(
        "provider_config.created",
        extra={
            "provider_config_id": provider_config.id,
            "provider": provider_config.provider,
            "user_id": user_id,
        },
    )
    return provider_config


async def update_provider_config(
    db: AsyncSession,
    user_id: int,
    provider_config: ProviderConfig,
    payload: ProviderConfigUpdate,
) -> ProviderConfig:
    """Update a provider config, replacing encrypted key only if provided."""
    await _lock_user_provider_configs(db, user_id)
    updates = payload.model_dump(exclude_unset=True)

    if updates.get("is_default") is True:
        await _clear_default_provider(db, user_id)
        provider_config.is_default = True
        user = await db.get(User, user_id)
        if user is not None:
            user.default_provider_id = provider_config.id
    elif updates.get("is_default") is False:
        provider_config.is_default = False
        user = await db.get(User, user_id)
        if user is not None and user.default_provider_id == provider_config.id:
            user.default_provider_id = None

    for field in ("name", "provider", "model"):
        if field in updates:
            setattr(provider_config, field, updates[field])

    if "api_key" in updates:
        provider_config.api_key_encrypted = encrypt_api_key(updates["api_key"])

    await db.commit()
    await db.refresh(provider_config)
    logger.info(
        "provider_config.updated",
        extra={
            "provider_config_id": provider_config.id,
            "provider": provider_config.provider,
            "user_id": user_id,
        },
    )
    return provider_config


async def delete_provider_config(
    db: AsyncSession,
    user_id: int,
    provider_config: ProviderConfig,
) -> None:
    """Delete a user-owned provider config."""
    await _lock_user_provider_configs(db, user_id)
    user = await db.get(User, user_id)
    if user is not None and user.default_provider_id == provider_config.id:
        user.default_provider_id = None

    await db.delete(provider_config)
    await db.commit()
    logger.info(
        "provider_config.deleted",
        extra={
            "provider_config_id": provider_config.id,
            "provider": provider_config.provider,
            "user_id": user_id,
        },
    )


def _strict_json_messages(
    messages: list[dict[str, str]],
    response_model: type[BaseModel],
    last_error: str | None = None,
) -> list[dict[str, str]]:
    schema = json.dumps(response_model.model_json_schema(), separators=(",", ":"))
    instruction = (
        "Output ONLY raw JSON conforming to this schema. "
        "No markdown, no preamble, no trailing text.\n"
        f"Schema: {schema}"
    )
    if last_error:
        instruction += f"\nPrevious response failed validation: {last_error}"
    return [*messages, {"role": "user", "content": instruction}]


def _extract_message_content(response: Any) -> str:
    """Extract text content from a LiteLLM response object or dict."""
    try:
        choice = response["choices"][0]
        message = choice["message"]
        content = message.get("content")
    except (KeyError, IndexError, TypeError, AttributeError):
        choice = response.choices[0]
        message = choice.message
        content = getattr(message, "content", None)

    if not isinstance(content, str) or not content.strip():
        raise ProviderCallError("Provider returned an empty response")
    return content


async def _completion(
    provider_config: ProviderConfig,
    api_key: str,
    messages: list[dict[str, str]],
    response_format: dict[str, str] | None = None,
) -> str:
    try:
        import litellm

        kwargs: dict[str, Any] = {
            "model": provider_config.model,
            "messages": messages,
            "api_key": api_key,
            "timeout": settings.LLM_TIMEOUT,
        }
        if provider_config.provider:
            kwargs["custom_llm_provider"] = provider_config.provider
        if response_format is not None:
            kwargs["response_format"] = response_format

        # asyncio.wait_for guarantees the timeout fires even when LiteLLM's
        # internal HTTP client does not raise (e.g. slow free-tier models that
        # queue the request without ever timing out at the TCP layer).
        try:
            response = await asyncio.wait_for(
                litellm.acompletion(**kwargs),
                timeout=settings.LLM_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise ProviderCallError(
                f"Provider did not respond within {settings.LLM_TIMEOUT}s (timeout)."
            )
        return _extract_message_content(response)
    except ProviderServiceError:
        raise
    except Exception as exc:
        msg = safe_provider_error_message(exc, api_key)
        raise ProviderCallError(msg) from exc


def extract_json_payload(raw_response: str) -> Any:
    """
    Extract and parse the outermost JSON object or array from LLM text.

    Known limitation: if a response contains multiple JSON structures, the
    first opener and last closer can produce an invalid slice. The caller's
    strict-prompt retry path handles that failure.
    """
    stripped = raw_response.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    candidates = []
    for opener, closer in (("{", "}"), ("[", "]")):
        start = stripped.find(opener)
        end = stripped.rfind(closer)
        if start != -1 and end != -1 and end > start:
            candidates.append((start, stripped[start : end + 1]))

    if not candidates:
        raise ProviderJSONError("Provider response did not contain JSON", raw_response)

    _, payload = min(candidates, key=lambda item: item[0])
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ProviderJSONError("Provider response contained invalid JSON", raw_response) from exc


def validate_json_response(raw_response: str, response_model: type[BaseModel]) -> BaseModel:
    """Parse and validate provider JSON against a Pydantic schema."""
    payload = extract_json_payload(raw_response)
    try:
        return response_model.model_validate(payload)
    except ValidationError as exc:
        raise ProviderJSONError("Provider JSON failed schema validation", raw_response) from exc


async def call_json_model(
    provider_config: ProviderConfig,
    messages: list[dict[str, str]],
    response_model: type[BaseModel],
    max_retries: int = 3,
) -> JSONCallResult:
    """Call a provider and require schema-valid JSON output."""
    api_key = decrypt_api_key(provider_config.api_key_encrypted)
    last_raw_response: str | None = None
    last_error: str | None = None
    native_json_available = True

    for _attempt in range(max_retries):
        try:
            raw_response = await _completion(
                provider_config,
                api_key,
                messages,
                response_format={"type": "json_object"} if native_json_available else None,
            )
            last_raw_response = raw_response
            data = validate_json_response(raw_response, response_model)
            return JSONCallResult(
                data=data,
                used_native_json=native_json_available,
                raw_response=raw_response,
            )
        except ProviderCallError as exc:
            safe_error = safe_provider_error_message(exc, api_key)
            if native_json_available:
                native_json_available = False
                last_error = safe_error
                messages = _strict_json_messages(messages, response_model, last_error)
                continue
            raise ProviderCallError(safe_error) from exc
        except ProviderJSONError as exc:
            native_json_available = False
            last_raw_response = (
                redact_provider_secret(exc.raw_response, [api_key])
                if exc.raw_response is not None
                else None
            )
            last_error = safe_provider_error_message(exc, api_key)
            messages = _strict_json_messages(messages, response_model, last_error)

    raise ProviderJSONError(
        "Provider failed to return schema-valid JSON after retries",
        last_raw_response,
    )


async def test_provider_config(
    db: AsyncSession,
    provider_config: ProviderConfig,
) -> tuple[bool, dict[str, Any], str | None]:
    """Test provider connectivity and store capability flags."""
    messages = [
        {
            "role": "user",
            "content": 'Return exactly this JSON object: {"ok": true}',
        }
    ]

    try:
        result = await call_json_model(provider_config, messages, CapabilityProbeResponse)
        flags = {
            "connectivity": True,
            "validated_json": True,
            "native_json": result.used_native_json,
        }
        provider_config.capability_flags = flags
        await db.commit()
        await db.refresh(provider_config)
        logger.info(
            "provider_config.test_succeeded",
            extra={
                "provider_config_id": provider_config.id,
                "provider": provider_config.provider,
            },
        )
        return True, flags, None
    except Exception as exc:
        api_key = None
        try:
            api_key = decrypt_api_key(provider_config.api_key_encrypted)
        except Exception:
            api_key = None
        error_msg = safe_provider_error_message(exc, api_key)
        flags = {
            "connectivity": False,
            "validated_json": False,
            "native_json": False,
            "error_type": exc.__class__.__name__,
            "error_detail": error_msg,
        }
        provider_config.capability_flags = flags
        await db.commit()
        await db.refresh(provider_config)
        logger.info(
            "provider_config.test_failed",
            extra={
                "provider_config_id": provider_config.id,
                "provider": provider_config.provider,
                "error_type": exc.__class__.__name__,
            },
        )
        return False, flags, error_msg
