"""Parse Xray config URIs into display metadata.

Panels hand back opaque URIs (``vless://``, ``vmess://``, ``trojan://``). The
app wants a name, a protocol, a host and a port to show in a list, so we
extract those.

Every function here is defensive: a config from a panel is data we do not
control, and a malformed one must degrade to "unknown" rather than raise. A
parse failure must never block provisioning — the URI still works in the VPN
core even when we cannot describe it.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import logging
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

logger = logging.getLogger(__name__)

SUPPORTED_SCHEMES = frozenset(
    {"vless", "vmess", "trojan", "ss", "ssr", "hysteria", "hysteria2", "tuic"}
)


@dataclass(slots=True)
class ParsedConfig:
    name: str
    protocol: str | None = None
    host: str | None = None
    port: int | None = None


def _decode_base64(value: str) -> str | None:
    """Decode base64 that may be missing its padding, as vmess URIs often are."""
    try:
        padded = value + "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None


def _parse_vmess(uri: str) -> ParsedConfig:
    """vmess:// carries a base64-encoded JSON object."""
    decoded = _decode_base64(uri[len("vmess://") :])
    if decoded is None:
        return ParsedConfig(name="vmess config", protocol="vmess")
    try:
        data = json.loads(decoded)
    except json.JSONDecodeError:
        return ParsedConfig(name="vmess config", protocol="vmess")

    port_raw = data.get("port")
    try:
        port = int(port_raw) if port_raw not in (None, "") else None
    except (TypeError, ValueError):
        port = None

    return ParsedConfig(
        name=str(data.get("ps") or data.get("add") or "vmess config")[:128],
        protocol="vmess",
        host=str(data.get("add"))[:255] if data.get("add") else None,
        port=port,
    )


def parse_config_uri(uri: str) -> ParsedConfig:
    """Best-effort metadata for a config URI. Never raises."""
    uri = (uri or "").strip()
    if not uri:
        return ParsedConfig(name="Unnamed config")

    try:
        scheme = uri.split("://", 1)[0].lower()
    except Exception:
        return ParsedConfig(name="Unnamed config")

    if scheme == "vmess":
        return _parse_vmess(uri)

    try:
        parsed = urlparse(uri)
        # The fragment is the human-readable name the panel set.
        name = unquote(parsed.fragment).strip() if parsed.fragment else ""
        host = parsed.hostname
        port = parsed.port

        protocol = scheme if scheme in SUPPORTED_SCHEMES else None
        if scheme == "vless":
            # The transport ("ws", "grpc", "tcp") is more useful to show than
            # the scheme, which is always "vless".
            params = parse_qs(parsed.query)
            transport = (params.get("type") or [""])[0]
            if transport:
                protocol = f"vless/{transport}"

        if not name:
            name = host or f"{scheme} config"

        return ParsedConfig(
            name=name[:128],
            protocol=(protocol or scheme)[:32],
            host=host[:255] if host else None,
            port=port,
        )
    except (ValueError, AttributeError):
        # urlparse raises ValueError on, for example, a bad port.
        logger.debug("config_uri_unparseable")
        return ParsedConfig(name=f"{scheme} config"[:128], protocol=scheme[:32])


def _is_unroutable(host: str | None) -> bool:
    """A host no client can reach: loopback, unspecified, or empty."""
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return ip.is_loopback or ip.is_unspecified


def is_probably_config(uri: str) -> bool:
    """Whether a string is a config URI a client could actually connect with.

    Panels sometimes include blank lines or a comment in their output; those
    should not become rows in the configs table.

    Panels also add informational entries shaped like configs — PasarGuard puts
    ``ss://...@127.0.0.1:1080#<username>`` and ``#<days left, traffic left>``
    around the real ones so that v2rayNG shows the text as a server name. They
    point at loopback and connect nowhere. Stored, the first of them became the
    customer's *default* server. They are dropped here; the same information
    comes from the subscription record itself.
    """
    uri = (uri or "").strip()
    if "://" not in uri or len(uri) < 12:
        return False
    if uri.split("://", 1)[0].lower() not in SUPPORTED_SCHEMES:
        return False
    return not _is_unroutable(parse_config_uri(uri).host)


def decode_subscription(body: str) -> list[str]:
    """Extract config URIs from a subscription response.

    A v2ray-style subscription is usually one base64 blob of newline-separated
    URIs, but some panels serve the URIs as plain text. Both are accepted.
    Anything that does not look like a supported config — blank lines, a
    comment, an HTML error page served with 200 — is dropped rather than
    stored, and duplicates keep their first position.
    """
    text = (body or "").strip()
    if not text:
        return []

    if "://" not in text:
        decoded = _decode_base64("".join(text.split()))
        if decoded is None:
            return []
        text = decoded

    seen: set[str] = set()
    uris: list[str] = []
    for line in text.splitlines():
        uri = line.strip()
        if is_probably_config(uri) and uri not in seen:
            seen.add(uri)
            uris.append(uri)
    return uris
