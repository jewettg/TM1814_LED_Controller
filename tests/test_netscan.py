import ipaddress
import socket
import threading

import pytest

from tm1814_led import netscan as N

MAC_IFCONFIG = """lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 192.168.1.20 netmask 0xffffff00 broadcast 192.168.1.255
utun4: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1280
\tinet 10.99.0.5 --> 10.99.0.5 netmask 0xffffffc0
en5: flags=8863<UP> mtu 1500
\tinet 169.254.7.7 netmask 0xffff0000
"""
LINUX_IP = ("1: lo    inet 127.0.0.1/8 scope host lo\n2: eth0    inet 192.168.1.20/24 brd 192.168.1.255 scope global eth0\n"
            "3: wg0    inet 10.8.0.2/32 scope global wg0\n4: docker0    inet 172.17.0.1/16 scope global docker0\n")
WINDOWS = """Ethernet adapter Ethernet:

   IPv4 Address. . . . . . . . . . . : 192.168.1.77
   Subnet Mask . . . . . . . . . . . : 255.255.255.0

Unknown adapter OpenVPN TAP-Windows:

   IPv4 Address. . . . . . . . . . . : 10.8.0.9
   Subnet Mask . . . . . . . . . . . : 255.255.255.252
"""


def names(text):
    return [(n, str(i)) for n, i in N.parse_interfaces(text)]


def test_parse_interfaces_on_each_operating_system():
    assert names(MAC_IFCONFIG) == [("en0", "192.168.1.20/24"), ("utun4", "10.99.0.5/26")]    # loopback + link-local dropped
    assert names(LINUX_IP) == [("eth0", "192.168.1.20/24"), ("wg0", "10.8.0.2/32"), ("docker0", "172.17.0.1/16")]
    assert names(WINDOWS) == [("Ethernet", "192.168.1.77/24"), ("OpenVPN TAP-Windows", "10.8.0.9/30")]


@pytest.mark.parametrize("name", ["utun4", "tun0", "wg0", "ppp0", "docker0", "vboxnet0", "awdl0", "OpenVPN TAP-Windows",
                                  "vEthernet (WSL)", "Cisco AnyConnect VPN"])
def test_tunnel_and_virtual_interfaces_are_recognized(name):
    assert N.TUNNEL_NAME.search(name)


@pytest.mark.parametrize("name", ["en0", "eth0", "wlan0", "Ethernet", "Wi-Fi", "enp3s0"])
def test_real_interfaces_are_not_mistaken_for_tunnels(name):
    assert not N.TUNNEL_NAME.search(name)


def test_local_networks_leave_out_vpns(monkeypatch):
    monkeypatch.setattr(N, "_run", lambda cmd, timeout=5.0: MAC_IFCONFIG if cmd[0] == "ifconfig" else "")
    assert [str(i) for i in N.local_networks()] == ["192.168.1.20/24"]
    assert len(N.local_networks(include_tunnels=True)) == 2


def test_sweep_hosts_skips_self_tiny_and_huge_networks_and_prefers_the_known_one():
    nets = [ipaddress.IPv4Interface("192.168.1.20/24"), ipaddress.IPv4Interface("10.8.0.2/32"),
            ipaddress.IPv4Interface("10.0.0.0/8"), ipaddress.IPv4Interface("8.8.8.8/24")]
    hosts = N.sweep_hosts(nets)
    assert len(hosts) == 253 and "192.168.1.20" not in hosts and "192.168.1.1" in hosts      # /32, /8 and public skipped
    other = ipaddress.IPv4Interface("192.168.50.9/24")
    assert len(N.sweep_hosts([nets[0], other])) == 506
    assert all(h.startswith("192.168.50.") for h in N.sweep_hosts([nets[0], other], near="192.168.50.200"))


def test_mac_normalization():
    for form in ("7c:3e:82:29:24:47", "7C-3E-82-29-24-47", "7C3E82292447", " 7c3e.8229.2447 "):
        assert N.normalize_mac(form) == "7C3E82292447"
    assert N.normalize_mac(None) == "" and N.normalize_mac("") == ""


class FakeResponder:
    """A UDP socket that answers the discovery message like a controller does."""

    def __init__(self, ip="127.0.0.1", mac="AABBCCDDEEFF"):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.reply = f"{ip},{mac},AK001-ZJ21413,tok".encode()
        self.got = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(1024)
            except OSError:
                return
            self.got.append(data)
            if data == b"HF-A11ASSISTHREAD":
                self.sock.sendto(self.reply, addr)

    def close(self):
        self.sock.close()


def test_discover_finds_a_controller_and_ignores_silence_and_noise():
    r = FakeResponder("192.168.1.99", "AA:BB:CC:DD:EE:FF".replace(":", ""))
    quiet = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); quiet.bind(("127.0.0.1", 0))
    try:
        found = N.discover(timeout=1.0, targets=[("127.0.0.1", r.port), ("127.0.0.1", quiet.getsockname()[1])])
        assert found == [{"ip": "192.168.1.99", "mac": "AABBCCDDEEFF", "model": "AK001-ZJ21413", "token": "tok"}]
        assert N.discover(timeout=0.4, targets=[("127.0.0.1", quiet.getsockname()[1])]) == []
        r.reply = b"garbage"
        assert N.discover(timeout=0.5, targets=[("127.0.0.1", r.port)]) == []
    finally:
        r.close(); quiet.close()


def test_find_by_mac_matches_any_spelling_and_rejects_other_devices():
    r = FakeResponder("192.168.1.99", "AABBCCDDEEFF")
    try:
        t = [("127.0.0.1", r.port)]
        assert N.find_by_mac("aa:bb:cc:dd:ee:ff", timeout=1.0, targets=t)["ip"] == "192.168.1.99"
        assert N.find_by_mac("11:22:33:44:55:66", timeout=0.6, targets=t) is None
        assert N.find_by_mac("", timeout=0.2, targets=t) is None
    finally:
        r.close()


def test_probe_asks_one_host():
    r = FakeResponder("127.0.0.1", "AABBCCDDEEFF")
    try:
        assert N.probe("127.0.0.1", timeout=1.0, port=r.port)["mac"] == "AABBCCDDEEFF"
        assert N.probe("127.0.0.1", timeout=0.3, port=1) is None
    finally:
        r.close()


def test_diagnosis_tells_the_cases_apart(monkeypatch):
    def setup(gateway_up, host_up, mac):
        monkeypatch.setattr(N, "default_gateway", lambda: "192.168.1.1")
        monkeypatch.setattr(N, "ping", lambda h, timeout=2.0: gateway_up if h == "192.168.1.1" else host_up)
        monkeypatch.setattr(N, "arp_mac", lambda h: mac)
    setup(False, False, "")
    assert "own router" in N.diagnose("192.168.1.50", "AABBCCDDEEFF")["summary"]
    setup(True, False, "")
    assert "off, has lost Wi-Fi, or has moved" in N.diagnose("192.168.1.50", "AABBCCDDEEFF")["summary"]
    setup(True, True, "112233445566")
    d = N.diagnose("192.168.1.50", "AABBCCDDEEFF")
    assert "another device" in d["summary"] and d["mac_matches"] is False
    setup(True, True, "AABBCCDDEEFF")
    assert "refuses the control connection" in N.diagnose("192.168.1.50", "AABBCCDDEEFF")["summary"]
