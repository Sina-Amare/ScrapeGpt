"""Tests for SSRF-safe URL validation."""

import pytest

from app.services.url_validator import (
    URLBlockReason,
    URLValidationError,
    resolve_validated_ip,
    validate_url,
    validate_redirect_target,
)


def _public_only_settings(monkeypatch):
    monkeypatch.setattr(
        "app.services.url_validator.settings",
        type("S", (), {"ALLOW_PRIVATE_NETWORK_URLS": False})(),
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


# ---------------------------------------------------------------------------
# resolve_validated_ip — connection-pinning helper (DNS-rebinding defense)
# ---------------------------------------------------------------------------


def test_resolve_validated_ip_returns_public_literal(monkeypatch):
    _public_only_settings(monkeypatch)
    assert resolve_validated_ip("http://93.184.216.34/page") == "93.184.216.34"


def test_resolve_validated_ip_blocks_private_literal(monkeypatch):
    _public_only_settings(monkeypatch)
    # A blocked address must not be returned as a pin target.
    assert resolve_validated_ip("http://127.0.0.1/secret") is None
    assert resolve_validated_ip("http://10.0.0.5/internal") is None


def test_resolve_validated_ip_resolves_hostname(monkeypatch):
    import socket
    _public_only_settings(monkeypatch)
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port: [(None, None, None, None, ("93.184.216.34", 0))],
    )
    assert resolve_validated_ip("https://example.com/x") == "93.184.216.34"


def test_resolve_validated_ip_none_when_resolves_private(monkeypatch):
    import socket
    _public_only_settings(monkeypatch)
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, port: [(None, None, None, None, ("127.0.0.1", 0))],
    )
    assert resolve_validated_ip("https://rebind.example/x") is None


def test_resolve_validated_ip_none_on_dns_failure(monkeypatch):
    import socket
    _public_only_settings(monkeypatch)

    def _boom(host, port):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    assert resolve_validated_ip("https://nope.example/x") is None


def test_resolve_validated_ip_none_without_hostname():
    assert resolve_validated_ip("not-a-url") is None
