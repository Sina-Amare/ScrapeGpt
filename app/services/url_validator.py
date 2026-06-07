"""SSRF-safe URL validation with per-hop redirect checking."""

from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse

from app.core.config import settings

logger = logging.getLogger(__name__)

# Cloud metadata endpoints that must always be blocked
_BLOCKED_IPS = frozenset(
    ipaddress.ip_address(ip)
    for ip in (
        "169.254.169.254",  # AWS/GCP/Azure instance metadata
        "100.100.100.200",  # Alibaba metadata
        "192.0.0.192",  # Oracle metadata (RFC 7526)
    )
)

_ALLOWED_SCHEMES = frozenset(("http", "https"))


class URLBlockReason(str, Enum):
    SCHEME_NOT_ALLOWED = "SCHEME_NOT_ALLOWED"
    PRIVATE_ADDRESS = "PRIVATE_ADDRESS"
    LOOPBACK = "LOOPBACK"
    LINK_LOCAL = "LINK_LOCAL"
    MULTICAST = "MULTICAST"
    RESERVED = "RESERVED"
    METADATA_IP = "METADATA_IP"
    DNS_FAILURE = "DNS_FAILURE"
    INVALID_URL = "INVALID_URL"
    TOO_MANY_REDIRECTS = "TOO_MANY_REDIRECTS"


@dataclass
class URLValidationError(Exception):
    reason: URLBlockReason
    message: str

    def __str__(self) -> str:
        return self.message


def _check_ip(ip_str: str) -> None:
    """Raise URLValidationError if ip_str is blocked."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        raise URLValidationError(
            URLBlockReason.INVALID_URL, f"Cannot parse IP address: {ip_str}"
        )

    if addr in _BLOCKED_IPS:
        raise URLValidationError(
            URLBlockReason.METADATA_IP,
            f"Blocked: metadata endpoint {ip_str}",
        )

    if settings.ALLOW_PRIVATE_NETWORK_URLS:
        return

    if addr.is_loopback:
        raise URLValidationError(URLBlockReason.LOOPBACK, f"Blocked: loopback address {ip_str}")
    if addr.is_link_local:
        raise URLValidationError(
            URLBlockReason.LINK_LOCAL, f"Blocked: link-local address {ip_str}"
        )
    if addr.is_multicast:
        raise URLValidationError(
            URLBlockReason.MULTICAST, f"Blocked: multicast address {ip_str}"
        )
    if addr.is_private:
        raise URLValidationError(
            URLBlockReason.PRIVATE_ADDRESS, f"Blocked: private address {ip_str}"
        )
    if addr.is_reserved:
        raise URLValidationError(
            URLBlockReason.RESERVED, f"Blocked: reserved address {ip_str}"
        )


def validate_url(url: str) -> str:
    """
    Validate a URL for safe fetching.

    Checks:
    - Scheme is http or https
    - Hostname resolves to a public IP (no private/loopback/link-local/multicast/reserved)
    - Not a known cloud metadata endpoint

    Returns the validated URL string on success.
    Raises URLValidationError on any block condition.
    """
    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise URLValidationError(URLBlockReason.INVALID_URL, f"Invalid URL: {exc}") from exc

    if not parsed.scheme:
        raise URLValidationError(URLBlockReason.INVALID_URL, f"Invalid URL: {url}")

    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise URLValidationError(
            URLBlockReason.SCHEME_NOT_ALLOWED,
            f"Scheme '{parsed.scheme}' not allowed. Use http or https.",
        )

    if not parsed.netloc:
        raise URLValidationError(URLBlockReason.INVALID_URL, f"Invalid URL: {url}")

    hostname = parsed.hostname
    if not hostname:
        raise URLValidationError(URLBlockReason.INVALID_URL, "URL has no hostname")

    # Try to parse hostname as a raw IP first
    try:
        ipaddress.ip_address(hostname)
        _check_ip(hostname)
        return url
    except URLValidationError:
        raise
    except ValueError:
        pass  # It's a hostname, not a raw IP — resolve it

    # Resolve all A/AAAA records
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise URLValidationError(
            URLBlockReason.DNS_FAILURE, f"DNS resolution failed for {hostname}: {exc}"
        ) from exc

    if not addr_infos:
        raise URLValidationError(
            URLBlockReason.DNS_FAILURE, f"No DNS records for {hostname}"
        )

    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip = sockaddr[0]
        _check_ip(ip)

    return url


def validate_redirect_target(location: str, original_url: str) -> str:
    """
    Validate a redirect target URL before following.

    Handles relative redirects by resolving against the original URL.
    Returns the absolute redirect URL on success.
    """
    if location.startswith("http://") or location.startswith("https://"):
        return validate_url(location)

    # Relative redirect — construct absolute URL from original
    parsed_orig = urlparse(original_url)
    if location.startswith("/"):
        absolute = f"{parsed_orig.scheme}://{parsed_orig.netloc}{location}"
    else:
        base = original_url.rsplit("/", 1)[0]
        absolute = f"{base}/{location}"

    return validate_url(absolute)
