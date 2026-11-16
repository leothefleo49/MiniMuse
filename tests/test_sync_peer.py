"""Device-to-device sync: the peer store, its endpoints, and pairing."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from nanomuse.sync.peer import (
    SyncPeerStore,
    add_pair,
    load_pairs,
    make_peer_router,
    remove_pair,
)


def client(tmp_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(make_peer_router(tmp_path / "sync-peer.db"), prefix="/v1/sync")
    return TestClient(app)


def test_push_and_pull_roundtrip(tmp_path: Path):
    c = client(tmp_path)
    out = c.post(
        "/v1/sync/changes",
        json={
            "device": "desk",
            "conversations": [{"cid": "chat-main-001", "kind": "main", "title": "Main"}],
            "messages": [
                {"mid": "msg-0001", "cid": "chat-main-001", "role": "user", "text": "hello"},
                {"mid": "msg-0002", "cid": "chat-main-001", "role": "assistant", "text": "hi"},
            ],
        },
    )
    assert out.status_code == 200
    body = out.json()
    assert body["accepted"] == 3
    assert body["rejected"] == []
    state = c.get("/v1/sync/state").json()
    assert state["enabled"] is False
    assert state["counts"] == {"conversations": 1, "messages": 2}
    assert state["cursor"] == 3
    changes = c.get("/v1/sync/changes?since=0").json()
    assert [m["mid"] for m in changes["messages"]] == ["msg-0001", "msg-0002"]
    assert changes["conversations"][0]["cid"] == "chat-main-001"
    assert c.get("/v1/sync/changes?since=3").json()["messages"] == []


def test_the_main_chat_is_one_for_the_pair(tmp_path: Path):
    c = client(tmp_path)
    c.post(
        "/v1/sync/changes",
        json={"conversations": [{"cid": "main-aaaa", "kind": "main", "title": "Main"}]},
    )
    out = c.post(
        "/v1/sync/changes",
        json={"conversations": [{"cid": "main-bbbb", "kind": "main", "title": "Other"}]},
    ).json()
    assert out["rejected"] == [
        {"cid": "main-bbbb", "reason": "main_exists", "cid_main": "main-aaaa"}
    ]


def test_a_message_needs_its_conversation(tmp_path: Path):
    c = client(tmp_path)
    out = c.post(
        "/v1/sync/changes",
        json={"messages": [{"mid": "msg-0001", "cid": "nope-0000", "role": "user", "text": "x"}]},
    ).json()
    assert out["rejected"][0]["reason"] == "no_conversation"


def test_a_rename_bumps_the_conversation_seq(tmp_path: Path):
    c = client(tmp_path)
    c.post(
        "/v1/sync/changes",
        json={"conversations": [{"cid": "side-0001", "kind": "side", "title": "Old"}]},
    )
    first = c.get("/v1/sync/changes?since=0").json()["conversations"][0]
    c.post(
        "/v1/sync/changes",
        json={"conversations": [{"cid": "side-0001", "kind": "side", "title": "New"}]},
    )
    changed = c.get(f"/v1/sync/changes?since={first['seq']}").json()
    assert changed["conversations"][0]["title"] == "New"


def test_tail_pull_and_skipped(tmp_path: Path):
    c = client(tmp_path)
    c.post(
        "/v1/sync/changes",
        json={
            "conversations": [{"cid": "side-0001", "kind": "side", "title": "One"}],
            "messages": [
                {"mid": f"msg-{i:04d}", "cid": "side-0001", "role": "user", "text": str(i)}
                for i in range(10)
            ],
        },
    )
    out = c.get("/v1/sync/changes?since=0&tail=3").json()
    assert [m["mid"] for m in out["messages"]] == ["msg-0007", "msg-0008", "msg-0009"]
    assert out["skipped"] == 7


def test_wipe_and_delete(tmp_path: Path):
    c = client(tmp_path)
    assert c.put("/v1/sync/state", json={"enabled": True}).json()["enabled"] is True
    c.post(
        "/v1/sync/changes",
        json={
            "conversations": [{"cid": "side-0001", "kind": "side", "title": "One"}],
            "messages": [{"mid": "msg-0001", "cid": "side-0001", "role": "user", "text": "x"}],
        },
    )
    c.delete("/v1/sync/conversations/side-0001")
    changes = c.get("/v1/sync/changes?since=0").json()
    assert changes["conversations"][0]["deleted"] is True
    c.delete("/v1/sync/changes")
    assert c.get("/v1/sync/state").json()["counts"]["messages"] == 0


def test_working_presence(tmp_path: Path):
    c = client(tmp_path)
    c.post(
        "/v1/sync/changes",
        json={"conversations": [{"cid": "side-0001", "kind": "side", "title": "One"}]},
    )
    posted = c.post(
        "/v1/sync/working",
        json={
            "cid": "side-0001",
            "working": True,
            "device": "phone",
            "state": "working",
            "detail": "opening",
        },
    )
    assert posted.status_code == 204
    working = c.get("/v1/sync/state").json()["working"]
    assert len(working) == 1
    assert working[0]["cid"] == "side-0001"
    assert working[0]["detail"] == "opening"
    c.post("/v1/sync/working", json={"cid": "side-0001", "working": False})
    assert c.get("/v1/sync/state").json()["working"] == []


def test_the_store_file_is_openable_twice(tmp_path: Path):
    store = SyncPeerStore(tmp_path / "db.sqlite")
    assert store.state()["cursor"] == 0
    assert SyncPeerStore(tmp_path / "db.sqlite").state()["cursor"] == 0


def test_pairing_helpers(tmp_path: Path):
    from nanomuse.vault.vault import CredentialVault

    data = tmp_path / "data"
    add_pair(data, "http://192.168.1.20:8787/", "sekrit-token", "Desk")
    pairs = load_pairs(data)
    assert pairs == [
        {"name": "Desk", "url": "http://192.168.1.20:8787", "key": "{{vault:PAIR_DESK}}"}
    ]
    # the token lives in the vault; pairs.json never carries it
    assert "sekrit-token" not in (data / "pairs.json").read_text(encoding="utf-8")
    vault = CredentialVault(data / "vault.enc", data / "vault.key")
    assert vault.resolve(pairs[0]["key"]) == "sekrit-token"
    # the same name replaces its entry, never duplicates it
    add_pair(data, "http://192.168.1.21:8787", "other", "Desk")
    assert [p["url"] for p in load_pairs(data)] == ["http://192.168.1.21:8787"]
    assert remove_pair(data, "Desk") is True
    assert load_pairs(data) == []
    assert remove_pair(data, "Desk") is False


def test_pairing_rejects_a_bad_address(tmp_path: Path):
    import pytest

    with pytest.raises(ValueError):
        add_pair(tmp_path / "data", "ftp://example.com", "tok")
