"""Tests for llmparser.extractors.block_detection."""

from llmparser.extractors.block_detection import detect_block

# ---------------------------------------------------------------------------
# Cloudflare
# ---------------------------------------------------------------------------

CLOUDFLARE_HTML = """<!DOCTYPE html>
<html>
<head><title>Just a moment...</title></head>
<body>
<h1>Just a moment...</h1>
<p>Checking your browser before accessing the site.</p>
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>
</body>
</html>"""


def test_cloudflare_title():
    result = detect_block(CLOUDFLARE_HTML, url="https://example.com", status_code=200)
    assert result.is_blocked is True
    assert result.block_type == "cloudflare"
    assert result.confidence >= 0.9


def test_cloudflare_body_signal():
    html = """<html><body>
    <div class="cf-challenge">Please wait...</div>
    <meta name="cf-ray" content="abc123">
    <p>This is a Cloudflare page.</p>
    </body></html>"""
    result = detect_block(html)
    assert result.is_blocked is True
    assert result.block_type == "cloudflare"


# ---------------------------------------------------------------------------
# CAPTCHA
# ---------------------------------------------------------------------------

RECAPTCHA_HTML = """<!DOCTYPE html>
<html>
<head><title>Verify you are human</title></head>
<body>
<div class="g-recaptcha" data-sitekey="abc123"></div>
<script src="https://www.google.com/recaptcha/api.js"></script>
</body>
</html>"""


def test_recaptcha():
    result = detect_block(RECAPTCHA_HTML, url="https://example.com", status_code=200)
    assert result.is_blocked is True
    assert result.block_type == "captcha"
    assert result.confidence >= 0.85


def test_hcaptcha():
    html = """<html><body>
    <div class="h-captcha" data-sitekey="xyz"></div>
    <script src="https://js.hcaptcha.com/1/api.js"></script>
    </body></html>"""
    result = detect_block(html)
    assert result.is_blocked is True
    assert result.block_type == "captcha"


def test_cf_turnstile():
    html = """<html><body>
    <div class="cf-turnstile" data-sitekey="key123"></div>
    </body></html>"""
    result = detect_block(html)
    assert result.is_blocked is True
    assert result.block_type == "captcha"


# ---------------------------------------------------------------------------
# DataDome
# ---------------------------------------------------------------------------

DATADOME_HTML = """<!DOCTYPE html>
<html>
<head><title>Access Denied</title></head>
<body>
<script>
window._dd_s = {rum: 1, id: "abc"};
</script>
<div id="ddCaptcha">Please verify you are human.</div>
</body>
</html>"""


def test_datadome():
    result = detect_block(DATADOME_HTML, url="https://example.com", status_code=200)
    assert result.is_blocked is True
    assert result.block_type == "datadome"
    assert result.confidence >= 0.9


# ---------------------------------------------------------------------------
# PerimeterX
# ---------------------------------------------------------------------------

PERIMETERX_HTML = """<!DOCTYPE html>
<html>
<head><title>Access Denied</title></head>
<body>
<script>
window._pxAppId = "PX12345";
</script>
<div class="px-captcha">Verify your identity.</div>
</body>
</html>"""


def test_perimeterx():
    result = detect_block(PERIMETERX_HTML, url="https://example.com", status_code=200)
    assert result.is_blocked is True
    assert result.block_type == "perimeterx"
    assert result.confidence >= 0.9


# ---------------------------------------------------------------------------
# Akamai
# ---------------------------------------------------------------------------

AKAMAI_HTML = """<!DOCTYPE html>
<html>
<head><title>Access Denied</title></head>
<body>
<script src="https://cdn.example.com/bmak.js"></script>
<p>Bot manager active.</p>
</body>
</html>"""


def test_akamai():
    result = detect_block(AKAMAI_HTML, url="https://example.com", status_code=200)
    assert result.is_blocked is True
    assert result.block_type == "akamai"
    assert result.confidence >= 0.85


def test_akamai_cookie_signal():
    html = """<html><head></head><body>
    <script>document.cookie='ak_bmsc=abc123';</script>
    <p>Content here.</p>
    </body></html>"""
    result = detect_block(html)
    assert result.is_blocked is True
    assert result.block_type == "akamai"


# ---------------------------------------------------------------------------
# Clean article (not blocked)
# ---------------------------------------------------------------------------

CLEAN_ARTICLE_HTML = """<!DOCTYPE html>
<html>
<head><title>How to bake bread — A complete guide</title></head>
<body>
<article>
<h1>How to bake bread</h1>
<p>Baking bread at home is a rewarding experience. This guide covers the
ingredients, techniques, and step-by-step instructions you need to produce a
perfect loaf. Whether you are a beginner or an experienced baker, you will
find something useful here. Let us start with the basics of yeast and flour
selection, then move on to kneading, proofing, and finally baking at the
correct temperature. The crust should be golden-brown and the interior soft.</p>
<p>You will need: flour, water, yeast, salt. Mix together, knead for 10
minutes, let rise for one hour, then bake at 220°C for 30 minutes.</p>
</article>
</body>
</html>"""


def test_clean_article_not_blocked():
    result = detect_block(CLEAN_ARTICLE_HTML, url="https://example.com/blog/bread", status_code=200)
    assert result.is_blocked is False
    assert result.block_type is None
    assert result.block_reason is None


# ---------------------------------------------------------------------------
# IP ban (403 + sparse content)
# ---------------------------------------------------------------------------

def test_ip_ban_403():
    html = "<html><body><h1>Forbidden</h1><p>Access denied.</p></body></html>"
    result = detect_block(html, url="https://example.com", status_code=403)
    assert result.is_blocked is True
    assert result.block_type == "ip_ban"
    assert result.confidence >= 0.9
    assert "403" in (result.block_reason or "")


def test_ip_ban_401():
    html = "<html><body><h1>Unauthorized</h1></body></html>"
    result = detect_block(html, status_code=401)
    assert result.is_blocked is True
    assert result.block_type == "ip_ban"


def test_403_with_rich_content_not_ip_ban():
    """403 with a full error page (>200 words) should NOT be classified as ip_ban."""
    # Generate a body with more than 200 words
    words = " ".join(["word"] * 210)
    html = f"<html><body><p>{words}</p></body></html>"
    result = detect_block(html, status_code=403)
    # word count > 200, so ip_ban rule does not fire
    assert result.block_type != "ip_ban"


# ---------------------------------------------------------------------------
# Empty page (HTTP 200, <20 words)
# ---------------------------------------------------------------------------

def test_empty_page():
    html = "<html><head></head><body><p>Loading...</p></body></html>"
    result = detect_block(html, url="https://example.com", status_code=200)
    assert result.is_blocked is True
    assert result.block_type == "empty"


def test_blank_page():
    html = "<html><body></body></html>"
    result = detect_block(html, status_code=200)
    assert result.is_blocked is True
    assert result.block_type == "empty"


# ---------------------------------------------------------------------------
# Soft block (sparse body + many external scripts)
# ---------------------------------------------------------------------------

def test_soft_block():
    # 7 external scripts + sparse visible text
    scripts = "\n".join(
        f'<script src="https://cdn{i}.example.com/js/app.js"></script>'
        for i in range(7)
    )
    html = f"""<html>
    <head><title>App</title></head>
    <body>
    {scripts}
    <div id="app">Loading</div>
    </body>
    </html>"""
    result = detect_block(html, url="https://example.com", status_code=200)
    assert result.is_blocked is True
    assert result.block_type == "soft_block"
    assert result.confidence >= 0.7


def test_soft_block_not_triggered_with_content():
    """Plenty of visible content should not trigger soft_block even with many scripts."""
    scripts = "\n".join(
        f'<script src="https://cdn{i}.example.com/js/app.js"></script>'
        for i in range(8)
    )
    words = " ".join(["word"] * 50)
    html = f"""<html>
    <head><title>Article</title></head>
    <body>
    {scripts}
    <article><p>{words}</p></article>
    </body>
    </html>"""
    result = detect_block(html, status_code=200)
    assert result.block_type != "soft_block"
