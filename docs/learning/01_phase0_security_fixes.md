# 01 — Phase 0 Security Fixes: Rate Limiting & Auth Hardening

> **Files:** `app/core/rate_limit.py`, `app/api/v1/endpoints/auth.py`, `tests/core/test_rate_limit.py`
> **Phase:** 0 (pre-feature stabilization)

---

## What Was Fixed

Two security issues in the rate limiting and auth layer, both introduced during the initial implementation.

---

## Fix 1 — Rate Limit Key Used Unverified Token (`rate_limit.py`)

### Problem

The rate-limit key function read the JWT from the Authorization header but called `decode_token` instead of `verify_token`:

```python
# WRONG — decode_token skips signature verification
payload = decode_token(token)
if payload:
    return f"user:{payload.sub}"
```

`decode_token` in `security.py` is an intentionally unverified decode (uses `options={"verify_signature": False}`). It exists for debugging only.

**Attack vector:** An attacker could forge a JWT signed with any secret key, set `sub` to a victim's user ID, and send requests. The rate limiter would bucket those requests under the victim's ID, exhausting their quota. The attacker could then rotate `sub` values to effectively bypass their own limit entirely, or lock out any user.

### Fix

Replace `decode_token` with `verify_token(token, token_type="access")`, which validates signature, expiry, and token type:

```python
from app.core.security import verify_token

def get_user_identifier(request: Request) -> str:
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        payload = verify_token(token, token_type="access")  # ← full validation
        if payload:
            return f"user:{payload.sub}"
    return get_remote_address(request)
```

**Invariant:** Rate limiting keys must only be derived from cryptographically verified claims. An invalid token falls back to IP-based limiting — the safe default.

---

## Fix 2 — `refresh_token` Endpoint Not Rate-Limited (`auth.py`)

### Problem

`register` and `login` endpoints were correctly decorated with `@limiter.limit(AUTH_RATE_LIMIT)`, but `refresh_token` was missed:

```python
# MISSING rate limit decorator
async def refresh_token(
    request: TokenRefreshRequest,   # ← also wrong: Pydantic body as first param
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
```

Additionally, the first parameter was named `request` but was typed as `TokenRefreshRequest` (a Pydantic model), not `starlette.requests.Request`. SlowAPI always expects the first parameter named `request` to be a Starlette `Request` object. Adding `@limiter.limit` to this signature without fixing the parameter would silently fall back to IP-based limiting.

### Fix

Add rate limit decorator, fix parameter order, rename the body parameter:

```python
@router.post("/refresh")
@limiter.limit(AUTH_RATE_LIMIT)
async def refresh_token(
    request: Request,                   # ← Starlette Request first
    payload: TokenRefreshRequest,       # ← body renamed to payload
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    token_payload = verify_token(payload.refresh_token, token_type="refresh")
    ...
```

**Why refresh needs rate limiting:** Refresh tokens have a 7-day TTL. An attacker with a stolen refresh token would otherwise have 7 days of unlimited token-refresh attempts.

---

## Tests Added (`tests/core/test_rate_limit.py`)

```python
def test_rate_limit_identifier_ignores_forged_jwt_subject():
    # Forge a JWT signed with wrong secret, claim to be "victim-user"
    forged_token = jwt.encode(
        {"sub": "victim-user", "type": "access", "exp": now + 5min},
        "wrong-secret",
        algorithm=settings.JWT_ALGORITHM,
    )
    # Must fall back to IP, not use the forged sub
    assert get_user_identifier(request_with(f"Bearer {forged_token}")) == "203.0.113.9"

def test_refresh_endpoint_is_rate_limited_with_request_parameter():
    # Verify the function signature has request: Request as first param
    # AND that the limiter has registered a limit for this endpoint
    signature = inspect.signature(auth.refresh_token)
    assert signature.parameters["request"].annotation is FastAPIRequest
    route_limits = limiter._route_limits.get(endpoint_name, [])
    assert any(limit.limit.amount == settings.RATE_LIMIT_AUTH_PER_MINUTE ...)
```

---

## Rule Going Forward

- **Always use `verify_token`, never `decode_token` in production paths.** `decode_token` should not exist outside debug tooling. If you need to extract a claim from a token without trusting it, that is itself a design smell.
- **SlowAPI parameter contract:** The function parameter named `request` must be `starlette.requests.Request`. If a route handler has a Pydantic body, it must be named differently (e.g., `payload`, `body`).
- **Auth endpoints that accept tokens must be rate-limited** — login, refresh, and password-reset alike.
