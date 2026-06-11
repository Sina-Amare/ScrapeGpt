"""Heuristics for detecting anti-bot challenge pages."""

from __future__ import annotations


def anti_bot_challenge_reason(html: str, final_url: str | None = None) -> str | None:
    """Return a reason when fetched HTML appears to be an anti-bot challenge.

    The detector is intentionally conservative: it looks for challenge-specific
    phrases and vendor markers, not just a provider name that could appear in a
    normal footer or script URL.
    """
    if not html:
        return None

    haystack = html[:100_000].lower()
    url = (final_url or "").lower()

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
    if "cf-turnstile" in haystack or "challenges.cloudflare.com/turnstile" in haystack:
        return "cloudflare_turnstile"
    if "hcaptcha" in haystack and ("captcha" in haystack or "challenge" in haystack):
        return "captcha_challenge"
    if "g-recaptcha" in haystack or "www.google.com/recaptcha/" in haystack:
        return "captcha_challenge"
    if "captcha" in url and ("verify" in haystack or "challenge" in haystack):
        return "captcha_challenge"

    return None
