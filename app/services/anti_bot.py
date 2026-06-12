"""Heuristics for detecting anti-bot challenge pages."""

from __future__ import annotations

# Human-readable messages for each challenge type.
CHALLENGE_MESSAGES: dict[str, str] = {
    "cloudflare_challenge": (
        "Cloudflare JS challenge detected. ScrapeGPT tried camoufox and "
        "stealth Playwright. If still blocked, run FlareSolverr: "
        "  docker run -d -p 8191:8191 flaresolverr/flaresolverr:latest "
        "then set FLARESOLVERR_URL=http://localhost:8191 in .env. "
        "Or add a saved browser session with valid cf_clearance cookies."
    ),
    "cloudflare_turnstile": (
        "Cloudflare Turnstile detected. The site requires an interactive "
        "challenge that headless browsers cannot pass. Solutions: "
        "(1) Run FlareSolverr (uses a real Chrome browser that can solve it): "
        "  docker run -d -p 8191:8191 flaresolverr/flaresolverr:latest "
        "  then set FLARESOLVERR_URL=http://localhost:8191 in .env. "
        "(2) Add a saved browser session with valid cf_clearance cookies."
    ),
    "captcha_challenge": (
        "CAPTCHA detected. Solutions: "
        "(1) Set CAPSOLVER_API_KEY in .env for automatic solving. "
        "(2) Add a saved browser session with cookies from a logged-in session."
    ),
}


def anti_bot_challenge_reason(html: str, final_url: str | None = None) -> str | None:
    """Return a reason string when the fetched HTML is an anti-bot challenge.

    Checks Turnstile FIRST because CF serves Turnstile as a fallback when
    a headless browser fails the initial JS fingerprint check.  When both
    cf-chl- AND cf-turnstile appear in the same page, Turnstile is the
    binding constraint — no amount of waiting will auto-solve it.
    """
    if not html:
        return None

    haystack = html[:100_000].lower()
    url = (final_url or "").lower()

    # Turnstile check comes first: it means the browser already failed the
    # JS fingerprint check and CF escalated to interactive Turnstile.
    if "cf-turnstile" in haystack or "challenges.cloudflare.com/turnstile" in haystack:
        return "cloudflare_turnstile"

    if (
        "cf-chl-" in haystack
        or "_cf_chl_opt" in haystack
        or "challenge-error-text" in haystack
        or "/cdn-cgi/challenge-platform/" in haystack
    ):
        return "cloudflare_challenge"
    if "enable javascript and cookies to continue" in haystack:
        return "cloudflare_challenge"
    if "just a moment" in haystack and "cloudflare" in haystack:
        return "cloudflare_challenge"
    if "checking if the site connection is secure" in haystack:
        return "cloudflare_challenge"

    if "hcaptcha" in haystack and ("captcha" in haystack or "challenge" in haystack):
        return "captcha_challenge"
    if "g-recaptcha" in haystack or "www.google.com/recaptcha/" in haystack:
        return "captcha_challenge"
    if "captcha" in url and ("verify" in haystack or "challenge" in haystack):
        return "captcha_challenge"

    return None


# Challenge types that a browser CAN auto-solve by executing JavaScript.
# Turnstile and CAPTCHA require an external solving service.
AUTO_SOLVABLE_CHALLENGES = frozenset({"cloudflare_challenge"})
