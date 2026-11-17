"""Find a Wear OS watch (and any other ADB wireless-debugging target) on the local network.

Why this is its own module: the first version sent one multicast query without choosing a network
interface and without asking for unicast replies. On a computer with several adapters (Tailscale,
WSL / Hyper-V, VPNs, Wi-Fi) the operating system sends such a query out of the lowest-metric adapter,
which is usually not the Wi-Fi the watch is on, and responders that answer in unicast never reply to
a query that did not ask for it. Result: "no watch found" even with the watch sitting on the LAN.

What it does now:

* queries **every** usable IPv4 interface at the same time (``IP_MULTICAST_IF`` per interface),
* sets the mDNS "unicast response" bit so replies can come straight back to the asking socket,
* **also listens on the mDNS group (port 5353)**: an Android phone answers by multicast, not unicast
  (verified on a real phone: 0 unicast packets, the service found 3 of 3 times via the group),
* resolves a service that only came back as a PTR record (asks again for its SRV record),
* falls back to the sender's address when the reply carried no A record,
* parses ``adb mdns services`` the same way, so either source can find the watch,
* probes the neighbours the computer already knows (ARP) for the old "Debug over Wi-Fi" port 5555.

Everything that touches the network takes a ``transport`` so tests never need real sockets.
"""

from __future__ import annotations

import contextlib
import ipaddress
import os
import re
import socket
import struct
import subprocess
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353
SVC_CONNECT = "_adb-tls-connect._tcp.local"
SVC_PAIRING = "_adb-tls-pairing._tcp.local"

TYPE_A = 1
TYPE_PTR = 12
TYPE_TXT = 16
TYPE_SRV = 33
QCLASS_IN = 1
QU_BIT = 0x8000

# adb names a wireless-debugging service ``adb-<serial>-<suffix>``
_SERVICE_SERIAL = re.compile(r"^adb-([A-Za-z0-9]+)-[A-Za-z0-9]+$")


# ------------------------------------------------------------------------------ small helpers
def service_serial(name: str | None) -> str | None:
    """The device serial inside an adb service name or adb device id, else ``None``.

    ``adb-PHONE0SERIAL-Ab12cD`` and ``adb-PHONE0SERIAL-Ab12cD._adb-tls-connect._tcp`` both give
    ``PHONE0SERIAL``. A plain ``192.168.1.5:5555`` has no serial in it.
    """
    if not name:
        return None
    base = name.strip().split(".", 1)[0] if name.startswith("adb-") else name.strip()
    m = _SERVICE_SERIAL.match(base)
    return m.group(1) if m else None


def _endpoint(name: str, host: str, port: int, kind: str) -> dict[str, Any]:
    return {
        "name": name,
        "host": host,
        "port": int(port),
        "host_port": f"{host}:{int(port)}",
        "kind": kind,
        "serial": service_serial(name),
    }


def is_lan_address(ip: str) -> bool:
    """A private LAN address that can carry mDNS: not loopback, link-local, multicast or CGNAT."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.version != 4:
        return False
    if addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified:
        return False
    if addr in ipaddress.ip_network("100.64.0.0/10"):  # CGNAT: Tailscale and friends, no multicast
        return False
    return addr.is_private


# ------------------------------------------------------------------------------ interfaces
def local_ipv4_addresses() -> list[str]:
    """Every usable IPv4 address of this computer, one per interface, without extra dependencies."""
    found: list[str] = []

    def add(ip: str) -> None:
        if ip and ip not in found:
            found.append(ip)

    with contextlib.suppress(OSError):
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(str(info[4][0]))
    # Linux / Android (the phone's Alpine capsule): ask each interface directly
    with contextlib.suppress(Exception):
        import fcntl  # type: ignore[import-not-found,unused-ignore]

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            for _idx, ifname in socket.if_nameindex():
                req = struct.pack("256s", ifname[:15].encode())
                res = fcntl.ioctl(s.fileno(), 0x8915, req)  # SIOCGIFADDR
                add(socket.inet_ntoa(res[20:24]))
    # the address the OS would use to reach a LAN: catches the case above that listed nothing
    for probe in ("192.168.255.255", "10.255.255.255", "172.31.255.255"):
        with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((probe, 9))
            add(str(s.getsockname()[0]))
    return [ip for ip in found if is_lan_address(ip)]


# ------------------------------------------------------------------------------ DNS wire format
def encode_name(name: str) -> bytes:
    out = bytearray()
    for label in name.strip(".").split("."):
        raw = label.encode("utf-8")
        out.append(len(raw))
        out.extend(raw)
    out.append(0)
    return bytes(out)


def build_query(questions: Iterable[tuple[str, int]], unicast: bool = True) -> bytes:
    """An mDNS query for ``(name, record type)`` pairs; ``unicast`` sets the QU bit."""
    qs = list(questions)
    pkt = bytearray(struct.pack("!HHHHHH", 0, 0, len(qs), 0, 0, 0))
    for name, rtype in qs:
        pkt += encode_name(name) + struct.pack("!HH", rtype, QCLASS_IN | (QU_BIT if unicast else 0))
    return bytes(pkt)


def read_name(pkt: bytes, offset: int) -> tuple[str, int]:
    labels: list[str] = []
    seen: set[int] = set()
    after = -1
    cur = offset
    while cur < len(pkt):
        if cur in seen:
            break
        seen.add(cur)
        length = pkt[cur]
        if length == 0:
            if after < 0:
                after = cur + 1
            break
        if (length & 0xC0) == 0xC0 and cur + 1 < len(pkt):
            if after < 0:
                after = cur + 2
            cur = ((length & 0x3F) << 8) | pkt[cur + 1]
            continue
        cur += 1
        labels.append(pkt[cur : cur + length].decode("utf-8", errors="replace"))
        cur += length
    return ".".join(labels), (after if after >= 0 else cur + 1)


class Records:
    """What one or more mDNS packets said: service instances, where they are, and their addresses."""

    def __init__(self) -> None:
        self.ptr: dict[str, set[str]] = {}  # service type (lower) -> instance full names
        self.srv: dict[str, tuple[str, int]] = {}  # instance (lower) -> (target host lower, port)
        self.addr: dict[str, str] = {}  # host name (lower) -> IPv4
        self.sender: dict[str, str] = {}  # instance (lower) -> IP the packet came from
        self.display: dict[str, str] = {}  # instance (lower) -> instance name as written

    def feed(self, pkt: bytes, sender: str) -> None:
        if len(pkt) < 12:
            return
        qd, an, ns, ar = struct.unpack("!HHHH", pkt[4:12])
        pos = 12
        try:
            for _ in range(qd):
                _, pos = read_name(pkt, pos)
                pos += 4
            for _ in range(an + ns + ar):
                if pos >= len(pkt):
                    break
                rname, pos = read_name(pkt, pos)
                if pos + 10 > len(pkt):
                    break
                rtype, _cls, _ttl, rdlen = struct.unpack("!HHIH", pkt[pos : pos + 10])
                pos += 10
                end = pos + rdlen
                if end > len(pkt):
                    break
                key = rname.lower()
                if rtype == TYPE_PTR:
                    inst, _ = read_name(pkt, pos)
                    self.ptr.setdefault(key, set()).add(inst)
                    self.display[inst.lower()] = inst
                elif rtype == TYPE_SRV and rdlen >= 6:
                    port = struct.unpack("!H", pkt[pos + 4 : pos + 6])[0]
                    target, _ = read_name(pkt, pos + 6)
                    self.srv[key] = (target.lower(), port)
                    self.sender[key] = sender
                    self.display.setdefault(key, rname)
                elif rtype == TYPE_A and rdlen == 4:
                    self.addr[key] = socket.inet_ntoa(pkt[pos : pos + 4])
                pos = end
        except (struct.error, IndexError):
            return  # a malformed packet from some other device on the network

    def unresolved(self) -> list[str]:
        """Instances a PTR record named whose SRV record has not arrived yet."""
        out: list[str] = []
        for insts in self.ptr.values():
            for inst in insts:
                if inst.lower() not in self.srv:
                    out.append(inst)
        return out

    def endpoints(self) -> dict[str, list[dict[str, Any]]]:
        connect: dict[str, dict[str, Any]] = {}
        pairing: dict[str, dict[str, Any]] = {}
        for inst, (target, port) in self.srv.items():
            if port <= 0:
                continue
            ip = self.addr.get(target) or self.sender.get(inst) or ""
            if not ip:
                continue
            short = self.display.get(inst, inst).split(".")[0]
            if inst.endswith("_adb-tls-pairing._tcp.local"):
                pairing.setdefault(f"{ip}:{port}", _endpoint(short, ip, port, "pairing"))
            elif inst.endswith("_adb-tls-connect._tcp.local"):
                connect.setdefault(f"{ip}:{port}", _endpoint(short, ip, port, "connect"))
        return {"connect": list(connect.values()), "pairing": list(pairing.values())}


# ------------------------------------------------------------------------------ transports
Transport = Callable[[str, bytes, float], list[tuple[bytes, str]]]


def _open_listener(iface_ip: str) -> socket.socket | None:
    """A socket on the mDNS port, joined to the mDNS group on one interface.

    Responders are allowed to answer a query *by multicast* (RFC 6762 section 5.4: when they have
    announced the record recently, even for a unicast-response question). An Android phone does
    exactly that, so a querier that only listens on its own ephemeral port hears nothing.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(("", MDNS_PORT))
        sock.setsockopt(
            socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(MDNS_GROUP) + socket.inet_aton(iface_ip)
        )
        sock.settimeout(0.1)
        return sock
    except OSError:
        sock.close()
        return None


def multicast_transport(iface_ip: str, packet: bytes, wait_s: float) -> list[tuple[bytes, str]]:
    """Send ``packet`` out of one interface and collect replies for ``wait_s`` seconds.

    Replies are read both where a unicast answer arrives (the asking socket) and where a multicast
    answer arrives (the mDNS group on port 5353).
    """
    asker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    listener = _open_listener(iface_ip)
    replies: list[tuple[bytes, str]] = []
    try:
        asker.bind((iface_ip, 0))
        asker.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface_ip))
        asker.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        asker.settimeout(0.1)
        asker.sendto(packet, (MDNS_GROUP, MDNS_PORT))
        resend_at = time.monotonic() + min(0.7, wait_s / 2)
        resent = False
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if not resent and time.monotonic() >= resend_at:
                with contextlib.suppress(OSError):
                    asker.sendto(packet, (MDNS_GROUP, MDNS_PORT))
                resent = True
            for sock in (asker, listener):
                if sock is None:
                    continue
                try:
                    data, addr = sock.recvfrom(9000)
                except (TimeoutError, BlockingIOError):
                    continue
                except OSError:
                    continue
                replies.append((data, str(addr[0])))
    except OSError:
        pass
    finally:
        asker.close()
        if listener is not None:
            listener.close()
    return replies


def browse_mdns(
    timeout_s: float = 1.6,
    interfaces: list[str] | None = None,
    transport: Transport | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Wireless-debugging endpoints on the LAN: ``{"connect": [...], "pairing": [...]}``.

    Skipped under pytest unless a ``transport`` is given, so the suite never depends on the network.
    """
    if transport is None:
        if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("NANOMUSE_NO_MDNS"):
            return {"connect": [], "pairing": []}
        transport = multicast_transport
    ifaces = interfaces if interfaces is not None else local_ipv4_addresses()
    records = Records()
    first = build_query([(SVC_CONNECT, TYPE_PTR), (SVC_PAIRING, TYPE_PTR)])
    send = transport

    def ask(iface: str, packet: bytes, wait: float) -> list[tuple[bytes, str]]:
        return send(iface, packet, wait)

    if not ifaces:
        return {"connect": [], "pairing": []}
    with ThreadPoolExecutor(max_workers=min(8, len(ifaces))) as pool:
        for batch in pool.map(lambda i: ask(i, first, timeout_s), ifaces):
            for data, sender in batch:
                records.feed(data, sender)
        # a service that came back as a PTR only: ask for its SRV (and address) directly
        pending = records.unresolved()
        if pending:
            second = build_query([(inst, TYPE_SRV) for inst in pending] + [(inst, TYPE_TXT) for inst in pending])
            for batch in pool.map(lambda i: ask(i, second, min(timeout_s, 1.0)), ifaces):
                for data, sender in batch:
                    records.feed(data, sender)
    return records.endpoints()


# ------------------------------------------------------------------------------ `adb mdns services`
def parse_adb_mdns_services(text: str) -> dict[str, list[dict[str, Any]]]:
    """Parse ``adb mdns services`` output (``name  _adb-tls-connect._tcp  192.168.1.9:37099``)."""
    connect: dict[str, dict[str, Any]] = {}
    pairing: dict[str, dict[str, Any]] = {}
    for line in (text or "").splitlines():
        parts = line.strip().split()
        if len(parts) < 3 or ":" not in parts[-1]:
            continue
        name, svc_type, hp = parts[0], parts[1], parts[-1]
        host, _, port_s = hp.rpartition(":")
        if not host or not port_s.isdigit():
            continue
        if "pairing" in svc_type:
            pairing[hp] = _endpoint(name, host, int(port_s), "pairing")
        elif "connect" in svc_type:
            connect[hp] = _endpoint(name, host, int(port_s), "connect")
    return {"connect": list(connect.values()), "pairing": list(pairing.values())}


def merge_endpoints(*sources: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Union of endpoint maps; the same ``host:port`` appears once."""
    out: dict[str, dict[str, dict[str, Any]]] = {"connect": {}, "pairing": {}}
    for src in sources:
        for kind in ("connect", "pairing"):
            for ep in src.get(kind, []):
                out[kind].setdefault(ep["host_port"], ep)
    return {k: list(v.values()) for k, v in out.items()}


# ------------------------------------------------------------------------------ legacy port 5555
def lan_neighbors(own: Iterable[str] = ()) -> list[str]:
    """LAN addresses this computer already knows about (its ARP table), minus its own."""
    text = ""
    arp_file = "/proc/net/arp"
    if os.path.isfile(arp_file):
        with contextlib.suppress(OSError), open(arp_file, encoding="utf-8") as fh:
            text = fh.read()
    if not text:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            from nanomuse.win_proc import hidden_subprocess_kwargs

            proc = subprocess.run(
                ["arp", "-a"], capture_output=True, text=True, timeout=4, check=False, **hidden_subprocess_kwargs()
            )
            text = proc.stdout if isinstance(proc.stdout, str) else ""
    mine = set(own)
    seen: list[str] = []
    for ip in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text):
        if ip in mine or ip in seen or not is_lan_address(ip) or ip.endswith((".0", ".255")):
            continue
        seen.append(ip)
    return seen


def probe_tcp_open(hosts: Iterable[str], port: int = 5555, timeout_s: float = 0.5) -> list[str]:
    """Which of ``hosts`` accept a connection on ``port`` (the legacy "Debug over Wi-Fi" port)."""
    targets = list(hosts)
    if not targets:
        return []

    def one(host: str) -> str | None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout_s)
        try:
            s.connect((host, port))
            return host
        except OSError:
            return None
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=min(16, len(targets))) as pool:
        return [h for h in pool.map(one, targets) if h]
