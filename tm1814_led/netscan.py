"""Finding the controller on the network, and working out why it cannot be reached.

The controller is identified by its MAC address, which never changes, while its IP address can (DHCP). Discovery asks
every host on the local networks (UDP port 48899) and the controller answers with `ip,MAC,model,token`.
Standard library only; every command-line helper is best effort and never raises.
"""
from __future__ import annotations

import ipaddress
import logging
import platform
import re
import select
import shutil
import socket
import subprocess
import time
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import protocol as P

log = logging.getLogger("tm1814.netscan")
MAX_SWEEP_HOSTS = 1024          # never sweep more than a /22 per interface


def normalize_mac(mac: Optional[str]) -> str:
    """'7c:3e:82:29:24:47', '7C-3E-82-29-24-47' and '7C3E82292447' all become '7C3E82292447'."""
    return re.sub(r"[^0-9A-Fa-f]", "", mac or "").upper()


def _run(cmd: Sequence[str], timeout: float = 5.0) -> str:
    if not shutil.which(cmd[0]):
        return ""
    try:
        return subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


# ---------------------------------------------------------------- this computer's networks

TUNNEL_NAME = re.compile(r"^(utun|tun|tap|ppp|wg|ipsec|gif|stf|awdl|llw|lo\d*|docker|br-|veth|virbr|vboxnet|vmnet|bridge)"
                         r"|vpn|tunnel|wireguard|tap-|virtual|vethernet|hyper-v|loopback|pseudo", re.IGNORECASE)


def parse_interfaces(text: str) -> List[Tuple[str, ipaddress.IPv4Interface]]:
    """(interface name, IPv4 address/prefix) from `ifconfig` (macOS/BSD), `ip -o -4 addr` (Linux) or `ipconfig`
    (Windows) output. Loopback and link-local addresses are skipped."""
    found: List[Tuple[str, ipaddress.IPv4Interface]] = []

    def add(name: str, ip: str, mask: str) -> None:
        try:
            iface = ipaddress.IPv4Interface(f"{ip}/{mask}")
        except ValueError:
            return
        if not (iface.ip.is_loopback or iface.ip.is_link_local) and (name, iface) not in found:
            found.append((name, iface))

    name = ""
    for line in text.splitlines():
        m = re.match(r"^\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", line)          # Linux: 2: eth0  inet 10.0.0.2/24
        if m:
            add(m.group(1), m.group(2), m.group(3))
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+):\s+flags=", line)                                    # BSD block header: en0: flags=
        if m:
            name = m.group(1)
            continue
        m = re.match(r"^\S[^\n]*?adapter\s+(.+?):\s*$", line, re.IGNORECASE)                  # Windows: Ethernet adapter Wi-Fi:
        if m:
            name = m.group(1)
            continue
        m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)(?:\s+-->\s+\S+)?\s+netmask\s+(0x[0-9a-fA-F]+)", line)   # BSD
        if m:
            add(name, m.group(1), str(ipaddress.IPv4Address(int(m.group(2), 16))))
            continue
        m = re.search(r"IPv4[^\n:]*:\s*(\d+\.\d+\.\d+\.\d+)", line)                          # Windows address line
        if m:
            pending = m.group(1)
            idx = text.index(line)
            mm = re.search(r"Subnet Mask[^\n:]*:\s*(\d+\.\d+\.\d+\.\d+)", text[idx:idx + 400])
            if mm:
                add(name, pending, mm.group(1))
    return found


def local_networks(include_tunnels: bool = False) -> List[ipaddress.IPv4Interface]:
    """This computer's IPv4 networks. VPN tunnels and virtual adapters are left out: a background search must never
    send probes into a corporate VPN or a container network."""
    for cmd in (["ip", "-o", "-4", "addr", "show"], ["ifconfig"], ["ipconfig"]):
        pairs = parse_interfaces(_run(cmd))
        if pairs:
            return [iface for name, iface in pairs if include_tunnels or not TUNNEL_NAME.search(name or "")]
    return []


def sweep_hosts(interfaces: Iterable[ipaddress.IPv4Interface], near: Optional[str] = None) -> List[str]:
    """Every other host address on the private networks this computer is on (/32s and huge nets skipped). With `near`
    (the controller's last known address), only the network containing it is swept, if this computer is on that one."""
    interfaces = list(interfaces)
    if near:
        try:
            ip = ipaddress.IPv4Address(near)
            local = [i for i in interfaces if ip in i.network]
            interfaces = local or interfaces
        except ValueError:
            pass
    hosts: List[str] = []
    for iface in interfaces:
        net = iface.network
        if not (iface.ip.is_private and 22 <= net.prefixlen <= 30):
            continue
        for h in net.hosts():
            if h != iface.ip and str(h) not in hosts:
                hosts.append(str(h))
                if len(hosts) >= MAX_SWEEP_HOSTS:
                    return hosts
    return hosts


# ---------------------------------------------------------------- discovery

def discover(timeout: float = 2.5, interfaces: Optional[Sequence[ipaddress.IPv4Interface]] = None, sweep: bool = True,
             targets: Optional[Sequence[Tuple[str, int]]] = None, port: int = P.DISCOVERY_PORT,
             near: Optional[str] = None) -> List[Dict[str, str]]:
    """Find controllers. Sends the discovery message by broadcast on every interface and, with `sweep`, directly to
    every host on those networks (some routers and access points drop broadcasts). `targets` replaces all of that
    with an explicit list of (address, port), which is how the tests use it."""
    if targets is None:
        nets = list(interfaces) if interfaces is not None else local_networks()
        addrs = ["255.255.255.255"] + [str(i.network.broadcast_address) for i in nets if i.network.prefixlen < 31]
        if sweep:
            addrs += sweep_hosts(nets, near)
        targets = [(a, port) for a in dict.fromkeys(addrs)]
    found: Dict[str, Dict[str, str]] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setblocking(False)
        pending = list(targets)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for _ in range(64):                           # send in small batches so replies are read as they arrive
                if not pending:
                    break
                addr = pending.pop(0)
                try:
                    sock.sendto(P.DISCOVERY_MESSAGE, addr)
                except OSError:
                    pass
            ready = select.select([sock], [], [], 0.05)[0]
            while ready:
                try:
                    data, _ = sock.recvfrom(1024)
                except (BlockingIOError, OSError):
                    break
                info = P.parse_discovery_reply(data)
                if info:
                    found[info["ip"]] = info
                ready = select.select([sock], [], [], 0)[0]
            if not pending and found and time.monotonic() > deadline - timeout * 0.5:
                break
    finally:
        sock.close()
    return list(found.values())


def find_by_mac(mac: str, **kwargs) -> Optional[Dict[str, str]]:
    """The discovery entry whose MAC matches, or None."""
    want = normalize_mac(mac)
    if not want:
        return None
    for entry in discover(**kwargs):
        if normalize_mac(entry.get("mac")) == want:
            return entry
    return None


def probe(host: str, timeout: float = 1.5, port: int = P.DISCOVERY_PORT) -> Optional[Dict[str, str]]:
    """Ask one host directly whether it is a controller (returns its discovery entry, which includes the MAC)."""
    found = discover(timeout=timeout, targets=[(host, port)])
    return found[0] if found else None


# ---------------------------------------------------------------- diagnosing "cannot reach it"

def default_gateway() -> Optional[str]:
    system = platform.system()
    if system == "Darwin":
        m = re.search(r"gateway:\s*(\S+)", _run(["route", "-n", "get", "default"]))
    elif system == "Linux":
        m = re.search(r"default via (\S+)", _run(["ip", "route", "show", "default"]))
    else:
        m = re.search(r"0\.0\.0\.0\s+0\.0\.0\.0\s+(\d+\.\d+\.\d+\.\d+)", _run(["route", "print", "0.0.0.0"]))
    return m.group(1) if m else None


def ping(host: str, timeout: float = 2.0) -> bool:
    system = platform.system()
    if system == "Windows":
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), host]
    elif system == "Darwin":
        cmd = ["ping", "-c", "1", "-t", str(max(1, int(timeout))), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout))), host]
    if not shutil.which(cmd[0]):
        return False
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout + 3).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def arp_mac(host: str) -> str:
    """The MAC this computer has for `host` on the local network (empty if unknown)."""
    system = platform.system()
    out = _run(["arp", "-n", host]) if system != "Windows" else _run(["arp", "-a", host])
    if not out and system == "Linux":
        out = _run(["ip", "neigh", "show", host])
    m = re.search(r"((?:[0-9a-fA-F]{1,2}[:-]){5}[0-9a-fA-F]{1,2})", out)
    if not m:
        return ""
    return normalize_mac(":".join(part.zfill(2) for part in re.split(r"[:-]", m.group(1))))


def diagnose(host: str, expected_mac: str = "") -> Dict[str, object]:
    """Why can this computer not reach the controller? Returns facts plus a plain-English `summary`."""
    gateway = default_gateway()
    gateway_ok = ping(gateway) if gateway else None
    host_ping = ping(host) if host else False
    seen_mac = arp_mac(host) if host else ""
    mac_ok = (not expected_mac) or (not seen_mac) or normalize_mac(seen_mac) == normalize_mac(expected_mac)
    if gateway and gateway_ok is False:
        summary = ("this computer cannot reach its own router, so the problem is the computer's network connection, "
                   "not the controller")
    elif host_ping and not mac_ok:
        summary = f"another device now has the address {host}, so the controller has probably moved to a new address"
    elif host_ping:
        summary = "the controller's address answers a ping but refuses the control connection (it may be restarting)"
    else:
        summary = ("the router is reachable but nothing answers at the controller's address: the controller is "
                   "off, has lost Wi-Fi, or has moved to a new address")
    return {"gateway": gateway, "gateway_ok": gateway_ok, "host_answers_ping": host_ping, "arp_mac": seen_mac,
            "mac_matches": mac_ok, "summary": summary}
