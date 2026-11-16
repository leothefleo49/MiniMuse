"""Tests for 365-day Trash, individual message deletion, Cloud-free P2P offline merge, and auto-update."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from nanomuse.config import Settings
from nanomuse.llm import MockLLM
from nanomuse.schema import LLMResponse, Message
from nanomuse.server import create_app
from nanomuse.server.selfupdate import SelfUpdate, is_editable_install
from nanomuse.server.service import MuseService


def _settings_for(root: Path, *, port: int = 8787, token: str = "tok") -> Settings:
    s = Settings()
    s.data_dir = root / "data"
    s.agent.workspace = root / "workspace"
    s.llm.api_key = "test"
    s.llm.stream = False
    s.server.port = port
    s.server.token = token
    s.ensure_dirs()
    return s


def _wait_idle(svc: MuseService, thread_id: str = "main", timeout: float = 5.0) -> None:
    t = svc.threads[thread_id]
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not t.busy and t.inbox.empty():
            return
        time.sleep(0.02)
    raise AssertionError(f"thread {thread_id} stayed busy")


class _RoutingTransport(httpx.AsyncBaseTransport):
    """Route peer HTTP requests in-process across ASGI apps with toggleable offline state."""

    def __init__(self, routes: dict[str, httpx.ASGITransport], online: dict[str, bool]):
        self.routes = routes
        self.online = online

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        key = f"{request.url.scheme}://{request.url.netloc.decode() if isinstance(request.url.netloc, bytes) else request.url.netloc}"
        if not self.online.get(key, True):
            raise httpx.ConnectError(f"simulated offline for {key}", request=request)
        transport = self.routes.get(key)
        if transport is None:
            raise httpx.ConnectError(f"unknown peer {key}", request=request)
        return await transport.handle_async_request(request)


# --------------------------------------------------------------------------- Trash (365-day retention)


def test_trash_lifecycle_restore_purge_and_expiry(tmp_path: Path) -> None:
    s = _settings_for(tmp_path)
    llm = MockLLM([LLMResponse(content="Alpha answer"), LLMResponse(content="Beta answer")])
    svc = MuseService(s, llm=llm)
    app = create_app(s, svc)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer tok"

        # 1. Create a side chat, send a turn, then soft-delete into Trash
        created = client.post("/api/threads", json={"title": "Research Notes"}).json()
        tid = created["id"]
        client.post(f"/api/threads/{tid}/send", json={"text": "Remember quantum pineapples"})
        _wait_idle(svc, tid)

        # Deleting main still returns 400 per API contract
        assert client.delete("/api/threads/main").status_code == 400

        del_res = client.delete(f"/api/threads/{tid}")
        assert del_res.status_code == 200
        assert del_res.json()["ok"] is True
        assert client.get(f"/api/threads/{tid}/events").status_code == 404

        state = client.get("/api/state").json()
        assert state["trash_count"] == 1

        trash = client.get("/api/trash").json()
        assert trash["retention_days"] == 365
        assert len(trash["items"]) == 1
        item = trash["items"][0]
        assert item["id"] == tid
        assert item["title"] == "Research Notes"
        assert item["retention_days"] == 365
        assert 363 <= item["days_left"] <= 365
        assert item["message_count"] == 2

        # 2. Restore from Trash brings timeline + agent session back intact
        restored = client.post(f"/api/trash/{tid}/restore").json()
        assert restored["ok"] is True
        assert restored["thread"]["id"] == tid
        events = client.get(f"/api/threads/{tid}/events").json()["events"]
        assert any(ev.get("text") == "Remember quantum pineapples" for ev in events)
        assert any(ev.get("text") == "Alpha answer" for ev in events)
        assert any("quantum pineapples" in str(m.content) for m in svc.threads[tid].agent.messages)
        assert client.get("/api/trash").json()["items"] == []

        # 3. Clearing main moves a was_main snapshot into Trash; restoring it yields a side chat
        client.post("/api/threads/main/send", json={"text": "Main thread line"})
        _wait_idle(svc, "main")
        assert client.post("/api/threads/main/clear").status_code == 200
        assert client.get("/api/threads/main/events").json()["events"] == []

        trash_after_clear = client.get("/api/trash").json()["items"]
        assert len(trash_after_clear) == 1
        assert trash_after_clear[0].get("was_main") is True
        snap_id = trash_after_clear[0]["id"]
        restored_main_snap = client.post(f"/api/trash/{snap_id}/restore").json()
        assert restored_main_snap["ok"] is True
        snap_events = client.get(
            f"/api/threads/{restored_main_snap['thread']['id']}/events"
        ).json()["events"]
        assert any(ev.get("text") == "Main thread line" for ev in snap_events)

        # 4. Expired items past expires_at are automatically pruned on listing
        svc.trash.append(
            {
                "id": "expired_old",
                "title": "Very Old Chat",
                "created_at": "2020-01-01T00:00:00Z",
                "updated_at": "2020-01-01T00:00:00Z",
                "deleted_at": "2020-01-01T00:00:00Z",
                "expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
                "retention_days": 365,
                "message_count": 1,
            }
        )
        svc._save_trash()
        listed_ids = [x["id"] for x in client.get("/api/trash").json()["items"]]
        assert "expired_old" not in listed_ids

        # 5. Permanent delete and empty trash
        c2 = client.post("/api/threads", json={"title": "Discard One"}).json()["id"]
        c3 = client.post("/api/threads", json={"title": "Discard Two"}).json()["id"]
        assert client.delete(f"/api/threads/{c2}").status_code == 200
        assert client.delete(f"/api/threads/{c3}").status_code == 200
        assert client.delete(f"/api/trash/{c2}").status_code == 200
        assert client.delete(f"/api/trash/{c2}").status_code == 404
        emptied = client.post("/api/trash/empty").json()
        assert emptied["ok"] is True
        assert emptied["removed"] >= 1
        assert client.get("/api/trash").json()["items"] == []


# --------------------------------------------------------------------------- Individual message deletion


def test_delete_single_message_and_multimodal_forget(tmp_path: Path) -> None:
    s = _settings_for(tmp_path)
    llm = MockLLM([LLMResponse(content="First assistant reply")])
    svc = MuseService(s, llm=llm)
    app = create_app(s, svc)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer tok"

        client.post("/api/threads/main/send", json={"text": "Unique phrase seven nine"})
        _wait_idle(svc, "main")
        events = client.get("/api/threads/main/events").json()["events"]
        user_ev = next(e for e in events if e.get("type") == "user")
        asst_ev = next(e for e in events if e.get("type") == "assistant")

        # Delete assistant message individually
        res = client.delete(f"/api/threads/main/events/{asst_ev['id']}")
        assert res.status_code == 200
        remaining = client.get("/api/threads/main/events").json()["events"]
        assert [e["id"] for e in remaining] == [user_ev["id"]]
        assert not any(
            m.role == "assistant" and "First assistant reply" in str(m.content)
            for m in svc.threads["main"].agent.messages
        )
        # Already deleted returns 404
        assert client.delete(f"/api/threads/main/events/{asst_ev['id']}").status_code == 404

        # Verify Agent.forget works with attachments and multimodal list content
        svc.threads["main"].agent.messages.append(
            Message(role="user", content="Look at photo", images=["photo.png"])
        )
        assert svc.threads["main"].agent.forget("user", "Look at photo", attachments_count=1)
        assert not any("Look at photo" in str(m.content) for m in svc.threads["main"].agent.messages)

        svc.threads["main"].agent.messages.append(
            Message.model_construct(
                role="user",
                content=[
                    {"type": "text", "text": "Multi part photo"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
                ],
            )
        )
        assert svc.threads["main"].agent.forget("user", "Multi part photo", attachments_count=1)
        assert not any("Multi part photo" in str(m.content) for m in svc.threads["main"].agent.messages)


# --------------------------------------------------------------------------- Cloud-free pairing & offline merge


def test_cloudless_direct_pairing_and_offline_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sa = _settings_for(tmp_path / "a", port=18781, token="tok-a")
    sb = _settings_for(tmp_path / "b", port=18782, token="tok-b")
    sa.hub.name = "Laptop-A"
    sb.hub.name = "Phone-B"
    svc_a = MuseService(sa, llm=MockLLM([LLMResponse(content="A reply")] * 4))
    svc_b = MuseService(sb, llm=MockLLM([LLMResponse(content="B reply")] * 4))

    monkeypatch.setenv("NANOMUSE_ADVERTISE_URLS", "http://peer-a.test")
    urls_a = ["http://peer-a.test"]
    urls_b = ["http://peer-b.test"]
    monkeypatch.delenv("NANOMUSE_ADVERTISE_URLS", raising=False)

    # Mock candidate_urls so A advertises peer-a.test and B advertises peer-b.test
    def _fake_urls(port: int, bind_host: str = "127.0.0.1") -> list[str]:
        return urls_a if int(port) == 18781 else urls_b

    monkeypatch.setattr("nanomuse.sync.discovery.candidate_urls", _fake_urls)
    monkeypatch.setattr("nanomuse.sync.engine.candidate_urls", _fake_urls)

    app_a = create_app(sa, svc_a)
    app_b = create_app(sb, svc_b)

    online = {"http://peer-a.test": True, "http://peer-b.test": True}
    routing = _RoutingTransport(
        {
            "http://peer-a.test": httpx.ASGITransport(app=app_a),
            "http://peer-b.test": httpx.ASGITransport(app=app_b),
        },
        online,
    )
    RealAsyncClient = httpx.AsyncClient

    class _PatchedAsyncClient(RealAsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = routing
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _PatchedAsyncClient)

    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        ca.headers["Authorization"] = "Bearer tok-a"
        cb.headers["Authorization"] = "Bearer tok-b"
        assert svc_a.hub.signed_in is False
        assert svc_b.hub.signed_in is False

        # B generates a 6-character pairing code + URI
        pair_info = cb.post("/api/sync/pair/code").json()
        code = pair_info["code"]
        assert len(code) == 6
        assert pair_info["pair_uri"].startswith("nanomuse://pair?")

        # Wrong pairing code fails cleanly without pairing
        bad = ca.post(
            "/api/sync/pair/claim",
            json={"target": "http://peer-b.test", "code": "WRONG1"},
        )
        assert bad.status_code == 400

        # Good pairing code succeeds without MiniMuse Cloud
        paired = ca.post(
            "/api/sync/pair/claim",
            json={"target": pair_info["pair_uri"]},
        )
        assert paired.status_code == 200
        assert paired.json()["ok"] is True

        # Ensure B also points to A cleanly with peer URL from claim
        # Now simulate BOTH devices going offline from each other!
        online["http://peer-a.test"] = False
        online["http://peer-b.test"] = False

        # Offline work on A: send message on main + create & rename a side chat
        ca.post("/api/threads/main/send", json={"text": "Written on Laptop-A while offline"})
        _wait_idle(svc_a, "main")
        side_a = ca.post("/api/threads", json={"title": "Side Draft A"}).json()
        ca.post(f"/api/threads/{side_a['id']}/send", json={"text": "Side A note"})
        _wait_idle(svc_a, side_a["id"])
        ca.patch(f"/api/threads/{side_a['id']}", json={"title": "Side Final A"})

        # Offline work on B: send message on main + create side chat on B
        cb.post("/api/threads/main/send", json={"text": "Written on Phone-B while offline"})
        _wait_idle(svc_b, "main")
        side_b = cb.post("/api/threads", json={"title": "Phone B Trip"}).json()
        cb.post(f"/api/threads/{side_b['id']}/send", json={"text": "Pack passport"})
        _wait_idle(svc_b, side_b["id"])

        # Attempting sync while offline saves locally & marks peer offline cleanly
        off_sync = ca.post("/api/sync/now").json()
        assert off_sync["pending_offline"] >= 0
        assert any(not p["online"] for p in off_sync["peers"])

        # Bring network back up and run bilateral sync + chronological merge
        online["http://peer-a.test"] = True
        online["http://peer-b.test"] = True
        ca.post("/api/sync/now")
        cb.post("/api/sync/now")
        ca.post("/api/sync/now")
        cb.post("/api/sync/now")

        main_a_texts = [e.get("text") for e in ca.get("/api/threads/main/events").json()["events"]]
        main_b_texts = [e.get("text") for e in cb.get("/api/threads/main/events").json()["events"]]
        assert "Written on Laptop-A while offline" in main_a_texts
        assert "Written on Phone-B while offline" in main_a_texts
        assert "Written on Laptop-A while offline" in main_b_texts
        assert "Written on Phone-B while offline" in main_b_texts

        # Both side chats and the offline rename reached the peer (each device keeps local t_* ids
        # mapped to the same global sync cid)
        threads_b_by_title = {t["title"]: t for t in cb.get("/api/threads").json()}
        threads_a_by_title = {t["title"]: t for t in ca.get("/api/threads").json()}
        assert "Side Final A" in threads_b_by_title
        assert "Side Draft A" not in threads_b_by_title
        assert "Phone B Trip" in threads_a_by_title
        side_a_on_b_events = [
            e.get("text")
            for e in cb.get(f"/api/threads/{threads_b_by_title['Side Final A']['id']}/events").json()["events"]
        ]
        assert "Side A note" in side_a_on_b_events
        side_b_on_a_events = [
            e.get("text")
            for e in ca.get(f"/api/threads/{threads_a_by_title['Phone B Trip']['id']}/events").json()["events"]
        ]
        assert "Pack passport" in side_b_on_a_events

        # Deleting one message on A replicates removal to B when synced
        ev_to_drop = next(
            e["id"]
            for e in ca.get("/api/threads/main/events").json()["events"]
            if e.get("text") == "Written on Phone-B while offline"
        )
        assert ca.delete(f"/api/threads/main/events/{ev_to_drop}").status_code == 200
        ca.post("/api/sync/now")
        cb.post("/api/sync/now")
        b_after = [e.get("text") for e in cb.get("/api/threads/main/events").json()["events"]]
        assert "Written on Phone-B while offline" not in b_after
        assert "Written on Laptop-A while offline" in b_after


# --------------------------------------------------------------------------- Auto-update settings & editable detection


def test_auto_update_settings_and_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings_for(tmp_path)
    svc = MuseService(s, llm=MockLLM([]))
    app = create_app(s, svc)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer tok"

        st = client.get("/api/update/status").json()
        assert st["auto_update"] is True
        assert "auto_supported" in st

        upd = client.put("/api/update/settings", json={"auto_update": False}).json()
        assert upd["ok"] is True
        assert upd["auto_update"] is False

        st2 = client.get("/api/update/status").json()
        assert st2["auto_update"] is False

    # Editable checkout detection still supports auto-update via git + wheel sync
    assert is_editable_install() is True
    su = SelfUpdate(None, current="1.0.15")
    info = su.status()
    assert info["editable"] is True
    assert info["auto_supported"] is True
