"""Watch discovery: finds wireless-debugging targets on every interface, even from sparse replies."""

from __future__ import annotations

import socket
import struct

from nanomuse.server import watch_discovery as wd

CONNECT = "_adb-tls-connect._tcp.local"
PAIRING = "_adb-tls-pairing._tcp.local"


def _rr(name: str, rtype: int, rdata: bytes) -> bytes:
    return wd.encode_name(name) + struct.pack("!HHIH", rtype, 1, 120, len(rdata)) + rdata


def _ptr(service: str, instance: str) -> bytes:
    return _rr(service, wd.TYPE_PTR, wd.encode_name(instance))


def _srv(instance: str, port: int, target: str) -> bytes:
    return _rr(instance, wd.TYPE_SRV, struct.pack("!HHH", 0, 0, port) + wd.encode_name(target))


def _a(host: str, ip: str) -> bytes:
    return _rr(host, wd.TYPE_A, socket.inet_aton(ip))


def _response(*records: bytes, answers: int | None = None) -> bytes:
    an = len(records) if answers is None else answers
    return struct.pack("!HHHHHH", 0, 0x8400, 0, an, 0, len(records) - an) + b"".join(records)


def _full_connect_reply(ip: str = "192.168.1.88", port: int = 38291, serial: str = "PHONE0SERIAL") -> bytes:
    inst = f"adb-{serial}-Ab12cD.{CONNECT}"
    return _response(_ptr(CONNECT, inst), _srv(inst, port, "Galaxy-Watch.local"), _a("Galaxy-Watch.local", ip))


# ------------------------------------------------------------------------------ helpers
def test_service_serial_from_names_and_device_ids() -> None:
    assert wd.service_serial("adb-PHONE0SERIAL-Ab12cD") == "PHONE0SERIAL"
    assert wd.service_serial("adb-PHONE0SERIAL-Ab12cD._adb-tls-connect._tcp") == "PHONE0SERIAL"
    assert wd.service_serial("192.168.1.5:5555") is None
    assert wd.service_serial("emulator-5562") is None
    assert wd.service_serial("") is None
    assert wd.service_serial(None) is None


def test_lan_address_filter_skips_tunnels_and_loopback() -> None:
    assert wd.is_lan_address("192.168.1.20")
    assert wd.is_lan_address("10.0.0.7")
    assert wd.is_lan_address("172.23.112.1")
    assert not wd.is_lan_address("100.64.1.10")  # Tailscale / CGNAT carries no multicast
    assert not wd.is_lan_address("127.0.0.1")
    assert not wd.is_lan_address("169.254.10.2")
    assert not wd.is_lan_address("224.0.0.251")
    assert not wd.is_lan_address("8.8.8.8")
    assert not wd.is_lan_address("not-an-ip")


def test_query_asks_for_unicast_replies() -> None:
    pkt = wd.build_query([(CONNECT, wd.TYPE_PTR)])
    qd = struct.unpack("!H", pkt[4:6])[0]
    assert qd == 1
    # the question ends with TYPE then CLASS; the top bit of CLASS is the "unicast response" bit
    qclass = struct.unpack("!H", pkt[-2:])[0]
    assert qclass & 0x8000
    assert not (struct.unpack("!H", wd.build_query([(CONNECT, wd.TYPE_PTR)], unicast=False)[-2:])[0] & 0x8000)


# ------------------------------------------------------------------------------ parsing
def test_full_reply_becomes_a_connect_endpoint() -> None:
    rec = wd.Records()
    rec.feed(_full_connect_reply(), "192.168.1.88")
    eps = rec.endpoints()
    assert eps["pairing"] == []
    (ep,) = eps["connect"]
    assert ep["host_port"] == "192.168.1.88:38291"
    assert ep["serial"] == "PHONE0SERIAL"
    assert ep["kind"] == "connect"
    assert ep["name"] == "adb-PHONE0SERIAL-Ab12cD"


def test_pairing_service_is_kept_apart_from_connect() -> None:
    inst = f"adb-WATCH1-xyz.{PAIRING}"
    rec = wd.Records()
    rec.feed(_response(_ptr(PAIRING, inst), _srv(inst, 40123, "w.local"), _a("w.local", "192.168.1.77")), "192.168.1.77")
    eps = rec.endpoints()
    assert eps["connect"] == []
    assert [e["host_port"] for e in eps["pairing"]] == ["192.168.1.77:40123"]


def test_missing_a_record_falls_back_to_the_sender_address() -> None:
    inst = f"adb-WATCH1-xyz.{CONNECT}"
    rec = wd.Records()
    rec.feed(_response(_ptr(CONNECT, inst), _srv(inst, 41000, "w.local")), "192.168.1.55")
    (ep,) = rec.endpoints()["connect"]
    assert ep["host_port"] == "192.168.1.55:41000"


def test_garbage_packets_are_ignored() -> None:
    rec = wd.Records()
    rec.feed(b"", "1.2.3.4")
    rec.feed(b"\x00" * 5, "1.2.3.4")
    rec.feed(b"\xff" * 64, "1.2.3.4")
    rec.feed(struct.pack("!HHHHHH", 0, 0, 0, 9, 0, 0) + b"\x05abc", "1.2.3.4")  # claims 9 answers, has none
    assert rec.endpoints() == {"connect": [], "pairing": []}


def test_adb_mdns_services_output_is_parsed() -> None:
    text = (
        "List of discovered mdns services\n"
        "adb-PHONE0SERIAL-Ab12cD\t_adb-tls-connect._tcp\t192.168.1.30:35347\n"
        "adb-WATCH9-abc\t_adb-tls-pairing._tcp\t192.168.1.77:40123\n"
        "garbage line without a port\n"
    )
    eps = wd.parse_adb_mdns_services(text)
    assert [e["host_port"] for e in eps["connect"]] == ["192.168.1.30:35347"]
    assert [e["host_port"] for e in eps["pairing"]] == ["192.168.1.77:40123"]
    assert eps["connect"][0]["serial"] == "PHONE0SERIAL"


def test_merge_lists_each_endpoint_once() -> None:
    a = wd.parse_adb_mdns_services("n\t_adb-tls-connect._tcp\t192.168.1.9:1111\n")
    b = wd.parse_adb_mdns_services("n\t_adb-tls-connect._tcp\t192.168.1.9:1111\nm\t_adb-tls-connect._tcp\t192.168.1.10:2222\n")
    merged = wd.merge_endpoints(a, b)
    assert sorted(e["host_port"] for e in merged["connect"]) == ["192.168.1.10:2222", "192.168.1.9:1111"]


# ------------------------------------------------------------------------------ browsing
def test_every_interface_is_queried_and_asks_for_unicast() -> None:
    seen: list[tuple[str, bytes]] = []

    def transport(iface: str, packet: bytes, wait: float) -> list[tuple[bytes, str]]:
        seen.append((iface, packet))
        # only the Wi-Fi interface reaches the watch
        return [(_full_connect_reply(), "192.168.1.88")] if iface == "192.168.1.20" else []

    ifaces = ["100.64.1.10", "172.23.112.1", "192.168.1.20"]
    eps = wd.browse_mdns(timeout_s=0.2, interfaces=ifaces, transport=transport)
    assert {i for i, _ in seen} == set(ifaces)  # the Tailscale-first bug: never only the first one
    assert all(struct.unpack("!H", p[-2:])[0] & 0x8000 for _, p in seen)
    assert [e["host_port"] for e in eps["connect"]] == ["192.168.1.88:38291"]


def test_ptr_only_reply_is_resolved_with_a_second_question() -> None:
    inst = f"adb-WATCH1-xyz.{CONNECT}"
    questions: list[int] = []

    def transport(_iface: str, packet: bytes, _wait: float) -> list[tuple[bytes, str]]:
        # first question is for PTR records, the follow-up is for the instance's SRV
        qtype = struct.unpack("!H", packet[-4:-2])[0]
        questions.append(qtype)
        if qtype == wd.TYPE_PTR:
            return [(_response(_ptr(CONNECT, inst)), "192.168.1.66")]
        return [(_response(_srv(inst, 42000, "w.local"), _a("w.local", "192.168.1.66")), "192.168.1.66")]

    eps = wd.browse_mdns(timeout_s=0.2, interfaces=["192.168.1.20"], transport=transport)
    assert [e["host_port"] for e in eps["connect"]] == ["192.168.1.66:42000"]
    assert wd.TYPE_PTR in questions
    assert any(q in (wd.TYPE_SRV, wd.TYPE_TXT) for q in questions)


def test_no_interfaces_means_no_endpoints() -> None:
    assert wd.browse_mdns(timeout_s=0.1, interfaces=[], transport=lambda *_: []) == {"connect": [], "pairing": []}


def test_browse_never_touches_the_network_under_pytest_without_a_transport() -> None:
    # PYTEST_CURRENT_TEST is set while this runs, so the real multicast transport must stay off
    assert wd.browse_mdns(timeout_s=0.1, interfaces=["192.168.1.20"]) == {"connect": [], "pairing": []}


def test_lan_neighbors_skips_own_broadcast_and_non_lan(monkeypatch) -> None:  # noqa: ANN001
    class P:
        stdout = (
            "Interface: 192.168.1.20 --- 0xb\n"
            "  192.168.1.1           70-58-a4-e6-cb-90     dynamic\n"
            "  192.168.1.30         aa-bb-cc-dd-ee-01     dynamic\n"
            "  192.168.1.20         aa-aa-aa-aa-aa-aa     dynamic\n"
            "  192.168.1.255         ff-ff-ff-ff-ff-ff     static\n"
            "  224.0.0.251           01-00-5e-00-00-fb     static\n"
        )

    monkeypatch.setattr(wd.os.path, "isfile", lambda _p: False)
    monkeypatch.setattr(wd.subprocess, "run", lambda *a, **k: P())
    assert wd.lan_neighbors(own=["192.168.1.20"]) == ["192.168.1.1", "192.168.1.30"]


def test_probe_tcp_open_finds_a_listening_host() -> None:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert wd.probe_tcp_open(["127.0.0.1"], port=port, timeout_s=1.0) == ["127.0.0.1"]
    finally:
        srv.close()
    assert wd.probe_tcp_open([], port=port) == []
