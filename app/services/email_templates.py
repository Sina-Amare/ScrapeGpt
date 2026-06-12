"""Branded HTML email templates.

Email clients ignore external/most embedded CSS, so every style here is inline
and the layout is table-based for broad compatibility. Each builder returns
``(subject, text, html)`` — the plain-text part is the fallback when HTML is not
rendered.
"""

_ACCENT = "#2272FF"
_INK = "#0F172A"
_MUTED = "#64748B"
_BG = "#F5F6FC"
_CARD = "#FFFFFF"
_BORDER = "#E2E8F0"
_HEADER_BG = "#0E0E12"


def _shell(title: str, intro: str, body_html: str, *, preheader: str = "") -> str:
    """Wrap content in the branded card layout."""
    return f"""\
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
  </head>
  <body style="margin:0;padding:0;background:{_BG};">
    <span style="display:none;opacity:0;color:transparent;height:0;width:0;overflow:hidden;">{preheader}</span>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_BG};padding:32px 12px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:{_CARD};border:1px solid {_BORDER};border-radius:16px;overflow:hidden;">
            <tr>
              <td style="background:{_HEADER_BG};padding:20px 28px;">
                <span style="font-family:Arial,Helvetica,sans-serif;font-size:18px;font-weight:800;color:#ffffff;letter-spacing:.2px;">Scrape<span style="color:{_ACCENT};">GPT</span></span>
              </td>
            </tr>
            <tr>
              <td style="padding:32px 28px;font-family:Arial,Helvetica,sans-serif;">
                <h1 style="margin:0 0 8px;font-size:20px;line-height:26px;color:{_INK};">{title}</h1>
                <p style="margin:0 0 20px;font-size:14px;line-height:22px;color:{_MUTED};">{intro}</p>
                {body_html}
              </td>
            </tr>
            <tr>
              <td style="padding:18px 28px;border-top:1px solid {_BORDER};font-family:Arial,Helvetica,sans-serif;">
                <p style="margin:0;font-size:12px;line-height:18px;color:{_MUTED};">ScrapeGPT — self-hosted, bring-your-own-key web data extraction. You received this email because someone used this address on a ScrapeGPT instance.</p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""


def welcome_email(email: str) -> tuple[str, str, str]:
    subject = "Welcome to ScrapeGPT"
    text = (
        "Welcome to ScrapeGPT!\n\n"
        "Your account is ready. Add an AI provider key, paste a URL, and start "
        "extracting structured data or clean content from any page.\n\n"
        "— The ScrapeGPT team"
    )
    body = f"""\
<p style="margin:0;font-size:14px;line-height:22px;color:{_INK};">
  Your account is ready. Add an AI provider key, paste a URL, and start
  extracting structured rows or clean content from any page — your data stays
  on your own hardware.
</p>"""
    html = _shell(
        "Welcome aboard 👋",
        f"You're all set up as {email}.",
        body,
        preheader="Your ScrapeGPT account is ready.",
    )
    return subject, text, html


def password_reset_email(code: str, ttl_minutes: int) -> tuple[str, str, str]:
    subject = "Your ScrapeGPT password reset code"
    text = (
        "Use this code to reset your ScrapeGPT password:\n\n"
        f"    {code}\n\n"
        f"It expires in {ttl_minutes} minutes. If you didn't request this, you "
        "can safely ignore this email."
    )
    body = f"""\
<div style="margin:0 0 18px;text-align:center;">
  <div style="display:inline-block;font-family:'Courier New',Courier,monospace;font-size:30px;font-weight:700;letter-spacing:8px;color:{_INK};background:{_BG};border:1px solid {_BORDER};border-radius:12px;padding:14px 22px;">{code}</div>
</div>
<p style="margin:0;font-size:13px;line-height:20px;color:{_MUTED};">This code expires in {ttl_minutes} minutes. If you didn't request a password reset, you can safely ignore this email — your password will not change.</p>"""
    html = _shell(
        "Reset your password",
        "Enter this code in the reset form to choose a new password.",
        body,
        preheader="Your ScrapeGPT password reset code",
    )
    return subject, text, html
