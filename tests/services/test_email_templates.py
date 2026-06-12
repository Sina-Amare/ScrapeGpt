"""Tests for branded email templates."""

from app.services.email_templates import password_reset_email, welcome_email


def test_password_reset_email_contains_code_and_ttl():
    subject, text, html = password_reset_email("123456", 15)
    assert subject
    assert "123456" in text
    assert "123456" in html
    assert "15 minutes" in text
    assert html.strip().lower().startswith("<!doctype html>")


def test_welcome_email_includes_address_and_subject():
    subject, text, html = welcome_email("user@example.com")
    assert "Welcome" in subject
    assert text
    assert "user@example.com" in html
    assert html.strip().lower().startswith("<!doctype html>")
