from app.services.anti_bot import anti_bot_challenge_reason


def test_detects_cloudflare_challenge_page():
    html = """
    <html>
      <title>Just a moment...</title>
      <script src="/cdn-cgi/challenge-platform/h/b/orchestrate/jsch/v1"></script>
      <body>Checking if the site connection is secure</body>
    </html>
    """

    assert anti_bot_challenge_reason(html, "https://example.com/") == "cloudflare_challenge"


def test_detects_oatd_cloudflare_403_body():
    html = """
    <!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title></head>
    <body><div class="main-wrapper" role="main">
    <div class="main-content"><noscript>
    <div class="h2"><span id="challenge-error-text">Enable JavaScript and cookies to continue</span></div>
    </noscript></div></div>
    <script>(function(){window._cf_chl_opt={cType:'managed'}}());</script>
    </body></html>
    """

    assert anti_bot_challenge_reason(html, "https://www.oatd.org/oatd/search") == "cloudflare_challenge"


def test_ignores_normal_cloudflare_asset_reference():
    html = """
    <html>
      <body>
        <h1>Research articles</h1>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/app.js"></script>
      </body>
    </html>
    """

    assert anti_bot_challenge_reason(html, "https://example.com/") is None
