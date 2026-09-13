from src.content_cleaner import clean_html_to_text, extract_emails
from src.link_discovery import discover_subpages


def test_clean_html_strips_scripts_and_nav():
    html = """
    <html><body>
        <nav><a href="/x">nav link</a></nav>
        <script>var x = 1;</script>
        <style>.a { color: red; }</style>
        <main><h1>Real content</h1><p>Hello world.</p></main>
        <footer>copyright 2026</footer>
    </body></html>
    """
    text = clean_html_to_text(html)
    assert "Real content" in text
    assert "Hello world." in text
    assert "var x" not in text
    assert "color: red" not in text
    assert "nav link" not in text
    assert "copyright" not in text


def test_clean_html_truncates_to_budget():
    html = "<p>" + ("word " * 5000) + "</p>"
    text = clean_html_to_text(html, max_chars=100)
    assert len(text) <= 140  # 100 + truncation marker
    assert text.endswith("truncated for token budget...]")


def test_extract_emails_finds_generic_addresses_only():
    html = """
    <a href="mailto:sales@acme.com">Sales</a>
    <a href="mailto:jane.doe@acme.com">Jane's personal inbox</a>
    <p>Or write to support@acme.com</p>
    """
    emails = extract_emails(html)
    assert "sales@acme.com" in emails
    assert "support@acme.com" in emails
    assert "jane.doe@acme.com" not in emails


def test_discover_subpages_ranks_and_dedupes():
    html = """
    <a href="/about">About</a>
    <a href="/about-our-security-practices">Security practices</a>
    <a href="https://external.com/about">External about (ignored)</a>
    <a href="/pricing">Pricing</a>
    <a href="/pricing">Pricing again (dedupe)</a>
    <a href="#anchor">Anchor (ignored)</a>
    """
    urls = discover_subpages(html, "https://acme.com/")
    assert "https://acme.com/about" in urls
    assert "https://acme.com/pricing" in urls
    assert not any("external.com" in u for u in urls)
    # exact "/about" should outrank the longer incidental match
    about_idx = urls.index("https://acme.com/about")
    security_idx = next(
        (i for i, u in enumerate(urls) if "security-practices" in u), len(urls)
    )
    assert about_idx < security_idx
