"""Tests for assess_html_quality — the gate that keeps undecodable/empty
fetches out of the LLM analyzer and the analysis cache."""

import zlib

from app.services.dom_summary import assess_html_quality

_GOOD_HTML = (
    "<!DOCTYPE html><html><head><title>Beef &amp; Veal</title></head>"
    "<body><h1>Calories</h1><table><tr><td>Beef Filet</td><td>143</td></tr>"
    "<tr><td>Veal Cutlet</td><td>215</td></tr></table></body></html>"
)


def test_good_html_is_usable():
    q = assess_html_quality(_GOOD_HTML)
    assert q.is_usable
    assert q.label == "ok"
    assert not q.is_binary
    assert q.replacement_ratio == 0.0


def test_undecoded_compressed_bytes_are_binary():
    """Compressed bytes decoded as text (the zstd/br bug) look binary."""
    raw = zlib.compress(_GOOD_HTML.encode("utf-8") * 50)
    garbled = raw.decode("utf-8", errors="replace")  # what the old fetcher produced
    q = assess_html_quality(garbled)
    assert q.is_binary
    assert q.label == "binary"
    assert q.replacement_ratio > 0.05


def test_replacement_char_heavy_text_is_binary():
    q = assess_html_quality("�" * 100 + "some text")
    assert q.is_binary


def test_empty_response_is_structureless():
    assert assess_html_quality("").label == "structureless"
    assert assess_html_quality("   \n\t ").label == "structureless"


def test_plain_text_summary_is_not_binary():
    """A DOM summary (plain text, few/no tags) must not be flagged binary."""
    summary = "Title: Example\nHeadings: Calories, Beef\nBody text snippet: ..."
    q = assess_html_quality(summary)
    assert not q.is_binary


def test_real_utf8_accents_are_not_binary():
    html = "<html><body><p>Café résumé naïve — façade</p></body></html>"
    q = assess_html_quality(html)
    assert q.is_usable
    assert not q.is_binary
