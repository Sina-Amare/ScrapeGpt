"""Tests for SSRF-safe URL validation."""

import pytest

from app.services.url_validator import (
    URLBlockReason,
    URLValidationError,
    validate_url,
    validate_redirect_target,
)


# ---------------------------------------------------------------------------
# Scheme validation
# ---------------------------------------------------------------------------


def test_rejects_ftp_scheme():
    with pytest.raises(URLValidationError) as exc_info:
        validate_url("ftp://example.com/file.txt")
    assert exc_info.value.reason == URLBlockReason.SCHEME_NOT_ALLOWED


def test_rejects_file_scheme():
    with pytest.raises(URLValidationError) as exc_info:
        validate_url("file:///etc/passwd")
    assert exc_info.value.reason == URLBlockReason.SCHEME_NOT_ALLOWED


def test_rejects_javascript_scheme():
    with pytest.raises(URLValidationError):
        validate_url("javascript:alert(1)")


# ---------------------------------------------------------------------------
# Private / reserved address blocking
# ---------------------------------------------------------------------------


def test_rejects_loopback_ip(monkeypatch):
    monkeypatch.setattr(
        "app.services.url_validator.settings",
        type("S", (), {"ALLOW_PRIVATE_NETWORK_URLS": False})(),
    )
    with pytest.raises(URLValidationError) as exc_info:
        validate_url("http://127.0.0.1/secret")
    assert exc_info.value.reason == URLBlockReason.LOOPBACK


def test_rejects_localhost_resolved(monkeypatch):
    import socket
    monkeypatch.setattr(
        "app.services.url_validator.settings",
        type("S", (), {"ALLOW_PRIVATE_NETWORK_URLS": False})(),
    )
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port: [(None, None, None, None, ("127.0.0.1", 0))]
    )
    with pytest.raises(URLValidationError) as exc_info:
        validate_url("http://localhost/admin")
    assert exc_info.value.reason == URLBlockReason.LOOPBACK


def test_rejects_private_ipv4(monkeypatch):
    monkeypatch.setattr(
        "app.services.url_validator.settings",
        type("S", (), {"ALLOW_PRIVATE_NETWORK_URLS": False})(),
    )
    with pytest.raises(URLValidationError) as exc_info:
        validate_url("http://192.168.1.1/")
    assert exc_info.value.reason == URLBlockReason.PRIVATE_ADDRESS


def test_rejects_private_10_block(monkeypatch):
    monkeypatch.setattr(
        "app.services.url_validator.settings",
        type("S", (), {"ALLOW_PRIVATE_NETWORK_URLS": False})(),
    )
    with pytest.raises(URLValidationError) as exc_info:
        validate_url("http://10.0.0.1/")
    assert exc_info.value.reason == URLBlockReason.PRIVATE_ADDRESS


def test_rejects_link_local(monkeypatch):
    monkeypatch.setattr(
        "app.services.url_validator.settings",
        type("S", (), {"ALLOW_PRIVATE_NETWORK_URLS": False})(),
    )
    with pytest.raises(URLValidationError) as exc_info:
        validate_url("http://169.254.1.1/")
    assert exc_info.value.reason == URLBlockReason.LINK_LOCAL


def test_rejects_metadata_ip_always(monkeypatch):
    # Metadata IP blocked even when ALLOW_PRIVATE_NETWORK_URLS=True
    monkeypatch.setattr(
        "app.services.url_validator.settings",
        type("S", (), {"ALLOW_PRIVATE_NETWORK_URLS": True})(),
    )
    with pytest.raises(URLValidationError) as exc_info:
        validate_url("http://169.254.169.254/latest/meta-data/")
    assert exc_info.value.reason == URLBlockReason.METADATA_IP


def test_allows_private_when_flag_set(monkeypatch):
    monkeypatch.setattr(
        "app.services.url_validator.settings",
        type("S", (), {"ALLOW_PRIVATE_NETWORK_URLS": True})(),
    )
    # Should not raise (127.0.0.1 allowed when flag is True, metadata still blocked)
    result = validate_url("http://127.0.0.1/test")
    assert result == "http://127.0.0.1/test"


# ---------------------------------------------------------------------------
# DNS failure
# ---------------------------------------------------------------------------


def test_rejects_dns_failure(monkeypatch):
    import socket
    monkeypatch.setattr(
        "app.services.url_validator.settings",
        type("S", (), {"ALLOW_PRIVATE_NETWORK_URLS": False})(),
    )
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port: (_ for _ in ()).throw(socket.gaierror("NXDOMAIN"))
    )
    with pytest.raises(URLValidationError) as exc_info:
        validate_url("http://nonexistent.invalid/")
    assert exc_info.value.reason == URLBlockReason.DNS_FAILURE


# ---------------------------------------------------------------------------
# Redirect validation
# ---------------------------------------------------------------------------


def test_redirect_target_validates_absolute(monkeypatch):
    monkeypatch.setattr(
        "app.services.url_validator.settings",
        type("S", (), {"ALLOW_PRIVATE_NETWORK_URLS": False})(),
    )
    with pytest.raises(URLValidationError) as exc_info:
        validate_redirect_target("http://192.168.0.1/evil", "http://example.com/page")
    assert exc_info.value.reason == URLBlockReason.PRIVATE_ADDRESS


def test_redirect_target_resolves_relative(monkeypatch):
    import socket
    monkeypatch.setattr(
        "app.services.url_validator.settings",
        type("S", (), {"ALLOW_PRIVATE_NETWORK_URLS": False})(),
    )
    # Mock DNS to return a public IP for example.com
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port: [(None, None, None, None, ("93.184.216.34", 0))]
    )
    result = validate_redirect_target("/other-page", "http://example.com/page")
    assert result == "http://example.com/other-page"
