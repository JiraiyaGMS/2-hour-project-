"""This computer on the local network: its addresses (for phones on the same Wi-Fi), and what kind they are."""

from __future__ import annotations

import ipaddress
import socket

VPN_NET = ipaddress.ip_network("100.64.0.0/10")  # carrier-grade NAT: what Tailscale and many VPNs hand out
LINK_LOCAL = ipaddress.ip_network("169.254.0.0/16")  # no router gave it an address


def kind_of(ip: str) -> str:
    """"lan" (a home or office network), "vpn", "none" (no address from a router), or "public"."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return "none"
    if a in LINK_LOCAL or a.is_loopback:
        return "none"
    if a in VPN_NET:
        return "vpn"
    return "lan" if a.is_private else "public"


def lan_addresses() -> list[str]:
    """This computer's IPv4 addresses, the likeliest home-network one first. With a VPN on, the address the
    internet goes out of is the VPN's, which a phone on the Wi-Fi can't reach: so every address counts."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))  # nothing is sent; this just picks the interface the internet uses
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        found += [info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        pass
    out: list[str] = []
    for ip in found:
        if ip not in out and kind_of(ip) != "none":
            out.append(ip)
    return sorted(out, key=lambda ip: {"lan": 0, "public": 1, "vpn": 2}[kind_of(ip)])


def lan_address() -> str | None:
    """The address a phone on the same Wi-Fi should use, or None."""
    ips = lan_addresses()
    return ips[0] if ips else None
