"""Regression tests for v1.2.22:
- Zero-config E2E encrypted Cloud Link (beacon + chunked async mailbox)
- Instant pairing code/QR popup via /v1/sync/pair/request & /v1/sync/pair/claim
- Automatic cloud_secret exchange during pairing & peer_sync_info upgrade
- Bulletproof deletion tombstones & anti-resurrection for chats, messages, memories, goals, reminders & apps
- PersonalCloudManager explicit vs auto_tunnel preference handling
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nanomuse.config import Settings
from nanomuse.server.api import create_app
from nanomuse.server.personal_cloud import PersonalCloudManager
from nanomuse.server.service import MuseService
from nanomuse.sync import cloud_link
from nanomuse.sync.peer import SyncPeerStore, add_pair


def _make_settings(tmp: Path, name: str) -> Settings:
    data_dir = tmp / name / "data"
    ws_dir = tmp / name / "workspace"
    data_dir.mkdir(parents=True, exist_ok=True)
    ws_dir.mkdir(parents=True, exist_ok=True)
    s = Settings()
    s.data_dir = data_dir
    s.agent.workspace = ws_dir
    s.server.host = "127.0.0.1"
    s.server.port = 8787
    s.server.token = "tok_" + name
    s.server.update_check = False
    return s


def test_cloud_link_e2e_crypto_beacon_and_chunked_mailbox(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = cloud_link.new_sync_secret()
    assert cloud_link.is_valid_sync_secret(secret)
    topic = cloud_link.derive_beacon_topic(secret)
    assert topic.startswith("nm2b-")

    env = cloud_link.encrypt_envelope(secret, topic, {"hello": "world", "n": 42})
    assert env.startswith("v1.")
    assert "world" not in env
    assert cloud_link.decrypt_envelope(secret, topic, env) == {"hello": "world", "n": 42}
    # Tamper / wrong secret / wrong topic AAD must fail cleanly
    assert cloud_link.decrypt_envelope(cloud_link.new_sync_secret(), topic, env) is None
    assert cloud_link.decrypt_envelope(secret, "nm2b-wrong", env) is None
    assert cloud_link.decrypt_envelope(secret, topic, env[:-3] + "AAA") is None

    # Local mock ntfy relay
    topics: dict[str, list[dict[str, Any]]] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def do_POST(self) -> None:
            tname = self.path.strip("/").split("/")[0]
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8")
            ev = {
                "id": f"m{len(topics.get(tname, [])) + 1}",
                "time": int(time.time()),
                "event": "message",
                "topic": tname,
                "message": body,
            }
            topics.setdefault(tname, []).append(ev)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def do_GET(self) -> None:
            tname = self.path.strip("/").split("/")[0]
            lines = "\n".join(json.dumps(e) for e in topics.get(tname, []))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            self.wfile.write(lines.encode("utf-8"))

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        relay = f"http://127.0.0.1:{port}"
        monkeypatch.setenv("NANOMUSE_CLOUD_LINK_RELAY", relay)

        async def _run() -> None:
            ok_b = await cloud_link.publish_beacon(
                secret,
                device_id="pc-1",
                name="Tyler-PC",
                kind="computer",
                urls=["https://fresh-demo.trycloudflare.com", "http://192.168.1.50:8787"],
                relay=relay,
            )
            assert ok_b
            beacons = await cloud_link.fetch_beacons(secret, exclude_device_id="phone-1", relay=relay)
            assert "pc-1" in beacons
            assert beacons["pc-1"]["urls"][0] == "https://fresh-demo.trycloudflare.com"

            # Chunked large mailbox packet (> 2800 chars compressed)
            big_text = "".join(f"turn-{i}-{hex(i * 7919)} " for i in range(600))
            ok_m = await cloud_link.publish_mailbox(
                secret,
                {
                    "type": "delta",
                    "packet_id": "pkt-big-1",
                    "device_id": "phone-1",
                    "messages": [{"mid": "m1", "text": big_text}],
                },
                relay=relay,
            )
            assert ok_m
            pkts, new_ids = await cloud_link.fetch_mailbox(
                secret, exclude_device_id="pc-1", seen_ids=set(), relay=relay
            )
            assert len(pkts) == 1
            assert pkts[0]["packet_id"] == "pkt-big-1"
            assert pkts[0]["messages"][0]["text"] == big_text
            assert "pkt-big-1" in new_ids
            # Seen packets are deduplicated on second poll
            pkts2, new_ids2 = await cloud_link.fetch_mailbox(
                secret, exclude_device_id="pc-1", seen_ids=set(new_ids), relay=relay
            )
            assert pkts2 == []
            assert new_ids2 == []

        asyncio.run(_run())
    finally:
        srv.shutdown()
        srv.server_close()


def test_pair_request_popup_and_cloud_secret_exchange(tmp_path: Path) -> None:
    svc = MuseService(_make_settings(tmp_path, "host"))
    app = create_app(svc.settings, service=svc)
    q = svc.bus.subscribe()

    with TestClient(app) as client:
        # 1. Remote device requests pairing -> host immediately pops up QR & code on bus without leaking code over HTTP
        resp = client.post(
            "/v1/sync/pair/request",
            json={
                "device_id": "phone-42",
                "name": "Pixel 9",
                "kind": "phone",
                "urls": ["http://192.168.1.99:8787"],
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["prompted"] is True
        assert "code" not in body

        # Drain bus and verify pair_request popup event arrived with code + QR
        popup = None
        while not q.empty():
            ev = q.get_nowait()
            if ev.get("kind") == "pair_request":
                popup = ev
        assert popup is not None
        assert popup["from_name"] == "Pixel 9"
        assert len(popup["code"]) >= 6
        assert popup["qr_png"]
        assert "nanomuse://pair?" in popup["pair_uri"]

        # 2. Claim with the popped-up code -> automatically agrees on shared E2E cloud_secret
        proposed = cloud_link.new_sync_secret()
        claim = client.post(
            "/v1/sync/pair/claim",
            json={
                "code": popup["code"],
                "device_id": "phone-42",
                "name": "Pixel 9",
                "kind": "phone",
                "urls": ["http://192.168.1.99:8787"],
                "peer_token": "nmpeer_return_tok_123456",
                "cloud_secret": proposed,
            },
        )
        assert claim.status_code == 200
        cdata = claim.json()
        assert cdata["ok"] is True
        assert cdata["cloud_secret"] == proposed

        # Verify host stored cloud_secret in vault and exposes cloud_link=True in peers_view()
        svc.sync.reload_peers()
        assert len(svc.sync.peers) == 1
        assert svc.sync.peers[0]["cloud_secret"] == proposed
        pv = svc.sync.peers_view()
        assert pv[0]["cloud_link"] is True
        assert pv[0]["remote_ready"] is True

        # 3. Attempting to claim when no code is active auto-pops up a fresh code on host!
        claim_nocode = client.post(
            "/v1/sync/pair/claim",
            json={"code": "XXXXXX", "device_id": "tablet-1", "name": "Galaxy Tab"},
        )
        assert claim_nocode.status_code == 401
        assert claim_nocode.json().get("prompted") is True
        popup2 = None
        while not q.empty():
            ev = q.get_nowait()
            if ev.get("kind") == "pair_request":
                popup2 = ev
        assert popup2 is not None
        assert popup2["from_name"] == "Galaxy Tab"


def test_tombstones_prevent_deleted_chats_and_muse_data_from_resurrecting(tmp_path: Path) -> None:
    svc_pc = MuseService(_make_settings(tmp_path, "pc"))
    svc_phone = MuseService(_make_settings(tmp_path, "phone"))

    # Create two side threads on PC and mirror them into sync-peer.db
    t_keep = svc_pc.create_thread("Keep Chat")
    t_keep.timeline.add({"type": "user", "text": "hello keep", "mid": "m_keep_0001"})
    t_keep.timeline.save()

    t_bad = svc_pc.create_thread("Broken Muse Import")
    t_bad.timeline.add({"type": "user", "text": "bad import row", "mid": "m_bad_00001"})
    t_bad.timeline.save()

    svc_pc.sync.mirror_local_now()
    bad_cid = svc_pc.sync.cid_of(t_bad.id)
    assert bad_cid

    # Simulate an older/broken delete that removed the thread without tombstoning sync-peer.db!
    svc_pc.threads.pop(t_bad.id, None)
    svc_pc.sync.state["cids"].pop(t_bad.id, None)
    svc_pc.sync._save()

    # Reconcile via mirror_local_now(): orphaned row in sync-peer.db MUST become deleted=1!
    svc_pc.sync.mirror_local_now()
    changes = svc_pc.sync.local_store.changes(0, 500, scope="all")
    by_cid = {c["cid"]: c for c in changes["conversations"]}
    assert by_cid[bad_cid]["deleted"] is True

    # Even if a stale peer tries to push the deleted conversation back (older updated_at),
    # SyncPeerStore AND _apply_conversation MUST NOT resurrect it!
    stale_push = svc_pc.sync.local_store.push(
        "stale-phone",
        [{"cid": bad_cid, "kind": "side", "title": "Broken Muse Import", "updated_at": time.time() - 60, "deleted": False}],
        [{"mid": "m_bad_00001", "cid": bad_cid, "role": "user", "text": "bad import row", "created_at": time.time() - 60}],
    )
    assert stale_push["accepted"] == 0
    svc_pc.sync._apply_conversation(
        {"cid": bad_cid, "kind": "side", "title": "Broken Muse Import", "updated_at": time.time() - 60, "deleted": False}
    )
    assert svc_pc.sync.thread_of(bad_cid) is None

    # Test Muse data tombstones: memory + workspace HTML app deleted on PC propagate to Phone
    mem_item = svc_pc.app.memory.add("Bad failed-sync memory", "general", source="import")
    ws_app = svc_pc.workspace() / "broken-app.html"
    ws_app.write_text("<html>bad</html>", encoding="utf-8")

    # Initial sync PC -> Phone copies both
    snap1 = svc_pc.sync.export_muse_data()
    svc_phone.sync.apply_muse_data(snap1)
    assert any(m.id == mem_item.id for m in svc_phone.app.memory.all())
    assert (svc_phone.workspace() / "broken-app.html").is_file()

    # Now delete both on PC (memory via note_deleted, app via file removal auto-detected!)
    svc_pc.app.memory.forget(mem_item.id)
    svc_pc.sync.note_deleted("memories", mem_item.id)
    ws_app.unlink()
    snap2 = svc_pc.sync.export_muse_data()
    assert mem_item.id in snap2["tombstones"]["memories"]
    assert "broken-app.html" in snap2["tombstones"]["apps"]

    # Phone applies snap2 -> deleted on Phone too!
    svc_phone.sync.apply_muse_data(snap2)
    assert not any(m.id == mem_item.id for m in svc_phone.app.memory.all())
    assert not (svc_phone.workspace() / "broken-app.html").is_file()

    # And if a stale snapshot tries to re-apply snap1 on PC or Phone, tombstones block resurrection!
    svc_pc.sync.apply_muse_data(snap1)
    assert not any(m.id == mem_item.id for m in svc_pc.app.memory.all())
    assert not (svc_pc.workspace() / "broken-app.html").is_file()


def test_personal_cloud_explicit_vs_default_auto_tunnel(tmp_path: Path) -> None:
    pc = PersonalCloudManager(port=8787, data_dir=tmp_path)
    assert pc.get_auto_tunnel_explicit() is None
    assert pc.get_auto_tunnel(default=False) is False
    assert pc.get_auto_tunnel(default=True) is True
    pc.set_auto_tunnel(False)
    assert pc.get_auto_tunnel_explicit() is False
    assert pc.get_auto_tunnel(default=True) is False
    pc.set_auto_tunnel(True)
    assert pc.get_auto_tunnel_explicit() is True
