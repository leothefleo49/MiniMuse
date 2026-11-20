"""Comprehensive regression tests covering:
1. Immediate settings persistence (profile + app-settings reload including allow_self_improve/show_thinking/language).
2. Sync pairing & auth security (CF-Connecting-IP proxy auth guard, unresolved vault placeholder guard,
   carrier NAT IP rejection, 401 token error parsing, pairing-link token refresh, and full workspace apps sync).
3. Complete AI UI context & SystemAdminTool get_settings/update_settings control.
4. Multi-AI exports (ChatGPT, Claude, Gemini Takeout JSON/HTML, Grok) & incremental/overlapping/edited re-exports.
"""

from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nanomuse import prompts
from nanomuse.cloud import CloudClient, CloudError
from nanomuse.config import Settings, apply_app_settings, load_app_settings
from nanomuse.server.api import create_app
from nanomuse.server.data_import import (
    ImportOptions,
    apply_plan,
    import_file,
    import_text,
    ingest_path,
    parse_ai_export_json,
    parse_takeout_html,
)
from nanomuse.server.realtime import _agent_instructions
from nanomuse.server.service import MuseService
from nanomuse.sync.engine import (
    _is_lan_only_url,
    _is_private_or_tailscale_ip,
    _is_raw_public_http_ip,
)
from nanomuse.sync.peer import add_pair
from nanomuse.tools.system_admin import SystemAdminTool
from nanomuse.schema import RiskLevel
from nanomuse.vault.vault import is_unresolved_placeholder


def _make_service(tmp_path: Path) -> MuseService:
    settings = Settings()
    settings.data_dir = tmp_path / "data"
    settings.agent.workspace = tmp_path / "workspace"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.agent.workspace.mkdir(parents=True, exist_ok=True)
    settings.server.token = "test-server-secret-token"
    return MuseService(settings)


# ------------------------------------------------------------------ 1. Settings persistence
def test_settings_immediate_persistence_and_reload(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    app = create_app(svc.settings, service=svc)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {svc.token}"}

    resp = client.put(
        "/api/settings",
        headers=headers,
        json={
            "profile": {
                "avatar_bg_mode": "default",
                "avatar_border_glow": 30,
                "avatar_animated": False,
            },
            "allow_self_improve": False,
            "show_thinking": True,
            "language": "English",
            "auto_update": False,
            "trash_retention_days": 14,
            "ui": {"theme": "dark", "accent": "#db2777"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["profile"]["avatar_bg_mode"] == "default"
    assert body["profile"]["avatar_animated"] is False
    assert body["agent"]["allow_self_improve"] is False
    assert body["agent"]["show_thinking"] is True
    assert body["agent"]["language"] == "English"
    assert body["auto_update"] is False
    assert body["trash_retention_days"] == 14

    # Verify reload from disk into a fresh Settings object preserves EVERY field
    fresh = Settings()
    fresh.data_dir = svc.data_dir
    stored = load_app_settings(svc.data_dir)
    apply_app_settings(fresh, stored)
    assert fresh.agent.allow_self_improve is False
    assert fresh.agent.show_thinking is True
    assert fresh.agent.language == "English"
    assert fresh.server.auto_update is False
    assert fresh.server.trash_retention_days == 14

    # Verify profile.json saved avatar_bg_mode="default" immediately
    prof_disk = json.loads((svc.data_dir / "profile.json").read_text(encoding="utf-8"))
    assert prof_disk["avatar_bg_mode"] == "default"


# ------------------------------------------------------------------ 2. Pairing, Auth, & Apps Sync
def test_proxy_headers_cannot_bypass_localhost_trust(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    app = create_app(svc.settings, service=svc)
    client = TestClient(app)

    # Local direct request (no proxy header) from testclient requires token because host != 127.0.0.1,
    # and a request carrying CF-Connecting-IP must ALWAYS be rejected without a valid token.
    r = client.get("/api/settings", headers={"CF-Connecting-IP": "216.49.181.250"})
    assert r.status_code == 401

    r_ok = client.get(
        "/api/settings",
        headers={
            "CF-Connecting-IP": "216.49.181.250",
            "Authorization": f"Bearer {svc.token}",
        },
    )
    assert r_ok.status_code == 200


def test_ip_classification_and_vault_placeholder_guards(tmp_path: Path) -> None:
    assert _is_private_or_tailscale_ip("192.168.1.42") is True
    assert _is_private_or_tailscale_ip("10.0.0.5") is True
    assert _is_private_or_tailscale_ip("100.88.12.34") is True
    assert _is_private_or_tailscale_ip("127.0.0.1") is False
    assert _is_private_or_tailscale_ip("216.49.181.250") is False

    assert _is_raw_public_http_ip("http://216.49.181.250:8787") is True
    assert _is_raw_public_http_ip("https://foo-bar.trycloudflare.com") is False
    assert _is_raw_public_http_ip("http://192.168.1.10:8787") is False
    assert _is_raw_public_http_ip("http://100.64.0.5:8787") is False

    assert _is_lan_only_url("http://216.49.181.250:8787") is True
    assert _is_lan_only_url("https://foo-bar.trycloudflare.com") is False
    assert _is_lan_only_url("http://my-pc.tailnet.ts.net:8787") is False

    assert is_unresolved_placeholder("{{vault:PAIR_MISSING}}") is True
    assert is_unresolved_placeholder("nmpeer_abc123") is False

    svc = _make_service(tmp_path)
    # _reachable_peer_urls must NEVER synthesize http://216.49.181.250:8787 from a carrier NAT IP
    urls = svc.sync._reachable_peer_urls(
        ["https://my-phone.trycloudflare.com", "http://192.168.1.25:8787"],
        client_ip="216.49.181.250",
    )
    assert "http://216.49.181.250:8787" not in urls
    assert "https://my-phone.trycloudflare.com" in urls
    assert "http://192.168.1.25:8787" in urls

    # Missing vault slot must resolve to empty token (NOT "{{vault:PAIR_GHOST}}") and flag auth_error
    (svc.data_dir / "pairs.json").write_text(
        json.dumps(
            [
                {
                    "id": "phone-ghost",
                    "name": "My Phone",
                    "url": "http://216.49.181.250:8787",
                    "urls": ["http://216.49.181.250:8787", "https://valid.trycloudflare.com"],
                    "key": "{{vault:PAIR_GHOST}}",
                }
            ]
        ),
        encoding="utf-8",
    )
    peers = svc.sync.reload_peers()
    assert peers[0]["token"] == ""
    assert peers[0]["url"] == "https://valid.trycloudflare.com"
    pv = svc.sync.peers_view()
    assert pv[0]["auth_error"] is True


def test_apps_manifest_and_sync_endpoints(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    ws = svc.workspace()
    # Create a Space/folder app, a standalone HTML app, junk node_modules, and a habit-tracker DB
    ht_dir = ws / "apps" / "habit-tracker"
    (ht_dir / "space-build" / "assets").mkdir(parents=True)
    (ht_dir / "node_modules" / "pkg").mkdir(parents=True)
    (ht_dir / "space.json").write_text(json.dumps({"name": "One For All", "description": "Habit hub"}), encoding="utf-8")
    (ht_dir / "icon.webp").write_bytes(b"RIFF....WEBP")
    (ht_dir / "space-build" / "index.html").write_text("<html><body>OFA</body></html>", encoding="utf-8")
    (ht_dir / "space-build" / "assets" / "app.js").write_text("console.log('ofa');", encoding="utf-8")
    (ht_dir / "node_modules" / "pkg" / "junk.js").write_text("junk", encoding="utf-8")
    (ws / "apps" / "index").mkdir(parents=True)
    (ws / "apps" / "index" / "index-import-2.html").write_text("<html>dup</html>", encoding="utf-8")
    (ws / "quick-calc.html").write_text("<html><body>Calc</body></html>", encoding="utf-8")

    # Create valid SQLite habit-tracker.db
    ht_db = svc.data_dir / "habit-tracker.db"
    conn = sqlite3.connect(ht_db)
    conn.execute("CREATE TABLE habits (id INTEGER PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO habits (title) VALUES ('Pushups')")
    conn.commit()
    conn.close()

    manifest = svc.sync.list_app_files()
    rels = {m["rel"] for m in manifest}
    assert "quick-calc.html" in rels
    assert "apps/habit-tracker/space-build/index.html" in rels
    assert "apps/habit-tracker/space-build/assets/app.js" in rels
    assert "apps/habit-tracker/icon.webp" in rels
    assert not any("node_modules" in r for r in rels)
    assert not any("-import-2.html" in r for r in rels)

    # Check /api/apps & /v1/sync/apps/* & /v1/sync/app-db/*
    app = create_app(svc.settings, service=svc)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {svc.token}"}

    r_apps = client.get("/api/apps", headers=headers)
    assert r_apps.status_code == 200
    slugs = {a["slug"]: a for a in r_apps.json()}
    assert "habit-tracker" in slugs
    assert slugs["habit-tracker"]["title"] == "One For All"
    assert slugs["habit-tracker"]["icon"] == "apps/habit-tracker/icon.webp"
    assert "quick-calc" in slugs

    r_file = client.get("/v1/sync/apps/apps/habit-tracker/space-build/index.html", headers=headers)
    assert r_file.status_code == 200
    assert "OFA" in r_file.text

    r_db = client.get("/v1/sync/app-db/habit-tracker.db", headers=headers)
    assert r_db.status_code == 200
    assert r_db.content[:16] == b"SQLite format 3\x00"

    r_escape = client.get("/v1/sync/apps/secret.py", headers=headers)
    assert r_escape.status_code == 403


# ------------------------------------------------------------------ 3. AI UI Context & SystemAdminTool
@pytest.mark.asyncio
async def test_ai_ui_context_and_system_admin_settings_control(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    # Prompt has full up-to-date UI & settings guidance and formats cleanly with zero brace errors
    assert "MiniMuse App UI, Settings & Honest Capabilities" in prompts.SYSTEM_PROMPT
    assert "Original (unedited)" in prompts.SYSTEM_PROMPT
    assert "system_admin" in prompts.SYSTEM_PROMPT
    assert "Account lives inside Settings, and Wear OS lives inside Devices" in prompts.SYSTEM_PROMPT
    formatted = prompts.SYSTEM_PROMPT.format(
        name="Sir Diddlage",
        language_rule="Reply in English.",
        workspace=str(tmp_path / "workspace"),
        sentinel_mode="ask",
        sandbox="sandboxed",
        contacts="",
        tool_names="system_admin, files",
        user_profile="",
        extra="",
        house_rules="- Be concise.",
    )
    assert "system_admin(action=\"update_settings\", settings={...})" in formatted

    call_prompt = _agent_instructions(svc)
    assert "Account & Personal Cloud" in call_prompt
    assert "Wear OS Smartwatch" in call_prompt
    assert "run_agent_task" in call_prompt

    tool = SystemAdminTool()
    tool.attach(svc)

    # Read-only assessment is SAFE; write assessment is SENSITIVE
    assert tool.assess({"action": "get_settings"}).risk == RiskLevel.SAFE
    assert tool.assess({"action": "update_settings", "settings": {"theme": "dark"}}).risk == RiskLevel.SENSITIVE

    # Update settings via tool (including shorthand top-level profile & ui keys!)
    res = await tool.execute(
        action="update_settings",
        settings={
            "avatar_bg_mode": "default",
            "theme": "dark",
            "allow_self_improve": False,
            "language": "English",
            "sync_side_chats": True,
        },
    )
    assert not res.error
    assert svc.profile.avatar_bg_mode == "default"
    assert svc.settings.agent.allow_self_improve is False
    assert svc.settings.agent.language == "English"

    snap = await tool.execute(action="get_settings")
    assert not snap.error
    parsed = json.loads(snap.output)
    assert parsed["profile"]["avatar_bg_mode"] == "default"
    assert parsed["ui"]["theme"] == "dark"
    assert parsed["agent"]["allow_self_improve"] is False
    assert parsed["sync"]["side_chats"] is True


# ------------------------------------------------------------------ 4. Multi-AI Exports & Incremental Re-Exports
def test_multi_ai_parsers_and_incremental_updated_reexports(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    # Initialize personal.db so FTS indexing runs
    pdb = svc.data_dir / "personal.db"
    sqlite3.connect(pdb).close()

    # A. ChatGPT export (v1) -> then updated export (v2) with renamed title, edited message, and new reply
    chatgpt_v1 = [
        {
            "id": "conv-gpt-1",
            "title": "Workout Plan",
            "current_node": "n2",
            "mapping": {
                "root": {"id": "root", "parent": None, "message": None},
                "n1": {
                    "id": "n1",
                    "parent": "root",
                    "message": {
                        "id": "m1",
                        "author": {"role": "user"},
                        "create_time": 1700000000,
                        "content": {"parts": ["Make a 3-day workout split"]},
                    },
                },
                "n2": {
                    "id": "n2",
                    "parent": "n1",
                    "message": {
                        "id": "m2",
                        "author": {"role": "assistant"},
                        "create_time": 1700000010,
                        "content": {"parts": ["Day 1: Push, Day 2: Pull, Day 3: Legs."]},
                    },
                },
            },
        }
    ]
    opts = ImportOptions()
    r1 = import_text(svc, json.dumps(chatgpt_v1), "conversations.json", opts)
    assert r1["threads_created"] == 1
    assert r1["events_added"] == 2
    assert r1["events_updated"] == 0

    # Now re-import an UPDATED export: renamed title, edited m2, plus new m3 & m4!
    chatgpt_v2 = [
        {
            "id": "conv-gpt-1",
            "title": "Workout Plan (Updated 4-Day)",
            "current_node": "n4",
            "mapping": {
                "root": {"id": "root", "parent": None, "message": None},
                "n1": {
                    "id": "n1",
                    "parent": "root",
                    "message": {
                        "id": "m1",
                        "author": {"role": "user"},
                        "create_time": 1700000000,
                        "content": {"parts": ["Make a 3-day workout split"]},
                    },
                },
                "n2": {
                    "id": "n2",
                    "parent": "n1",
                    "message": {
                        "id": "m2",
                        "author": {"role": "assistant"},
                        "create_time": 1700000010,
                        "content": {"parts": ["Day 1: Push, Day 2: Pull, Day 3: Legs, Day 4: Mobility."]},
                    },
                },
                "n3": {
                    "id": "n3",
                    "parent": "n2",
                    "message": {
                        "id": "m3",
                        "author": {"role": "user"},
                        "create_time": 1700000100,
                        "content": {"parts": ["Add core work too"]},
                    },
                },
                "n4": {
                    "id": "n4",
                    "parent": "n3",
                    "message": {
                        "id": "m4",
                        "author": {"role": "assistant"},
                        "create_time": 1700000120,
                        "content": {"parts": ["Added planks and hanging leg raises on Day 2 and Day 4."]},
                    },
                },
            },
        }
    ]
    r2 = import_text(svc, json.dumps(chatgpt_v2), "conversations.json", opts)
    assert r2["threads_created"] == 0
    assert r2["threads_merged"] == 1
    assert r2["duplicates"] == 1  # m1 unchanged
    assert r2["events_updated"] == 1  # m2 edited in place
    assert r2["events_added"] == 2  # m3 & m4 appended

    # Verify thread count, updated title, no duplicate bubbles, and clean FTS index
    imported_threads = [t for t in svc.threads.values() if t.id != "main"]
    assert len(imported_threads) == 1
    th = imported_threads[0]
    assert th.title == "Workout Plan (Updated 4-Day)"
    assert len(th.timeline.events) == 4
    assert "Day 4: Mobility" in th.timeline.events[1]["text"]
    assert "hanging leg raises" in th.timeline.events[3]["text"]

    conn = sqlite3.connect(pdb)
    fts_rows = conn.execute("SELECT content FROM messages_fts WHERE thread_id = ?", (th.id,)).fetchall()
    conn.close()
    assert len(fts_rows) == 4
    assert any("Day 4: Mobility" in row[0] for row in fts_rows)

    # B. Claude, Grok, Gemini Takeout JSON & HTML parsers
    claude_json = json.dumps(
        [
            {
                "uuid": "cl-1",
                "name": "Claude Chat",
                "chat_messages": [
                    {"uuid": "cm1", "sender": "human", "text": "Hello Claude", "created_at": "2026-10-01T10:00:00Z"},
                    {"uuid": "cm2", "sender": "assistant", "text": "Hi there!", "created_at": "2026-10-01T10:00:05Z"},
                ],
            }
        ]
    )
    claude_convs = parse_ai_export_json(claude_json, "conversations.json")
    assert len(claude_convs) == 1
    assert claude_convs[0].external_id == "claude:cl-1"
    assert len(claude_convs[0].turns) == 2

    grok_json = json.dumps(
        {
            "conversations": [
                {
                    "conversation": {"id": "gk-9", "title": "Grok Session"},
                    "responses": [
                        {"response": {"_id": "g1", "sender": "human", "message": "Yo Grok", "create_time": {"$date": {"$numberLong": "1730000000000"}}}},
                        {"response": {"_id": "g2", "sender": "assistant", "message": "Sup!", "create_time": {"$date": {"$numberLong": "1730000005000"}}}},
                    ],
                }
            ]
        }
    )
    grok_convs = parse_ai_export_json(grok_json, "prod-grok-backend.json")
    assert len(grok_convs) == 1
    assert grok_convs[0].external_id == "grok:gk-9"
    assert len(grok_convs[0].turns) == 2

    gemini_json = json.dumps(
        [
            {
                "header": "Gemini Apps",
                "title": "Prompted Explain quantum tunneling",
                "time": "2026-11-01T15:30:00Z",
                "safeHtmlItem": [{"html": "<p>Particles pass through barriers.</p>"}],
            }
        ]
    )
    gem_convs = parse_ai_export_json(gemini_json, "MyActivity.json")
    assert len(gem_convs) == 1
    assert gem_convs[0].turns[0].text == "Explain quantum tunneling"
    assert "Particles pass through barriers" in gem_convs[0].turns[1].text

    gemini_html = (
        '<div class="outer-cell">Prompted What is 2+2?<br>Nov 2, 2026, 3:15 PM<br>It is 4.</div>'
    )
    gem_h_convs = parse_takeout_html(gemini_html, "MyActivity.html")
    assert len(gem_h_convs) == 1
    assert gem_h_convs[0].turns[0].text == "What is 2+2?"

    # C. Updated ZIP re-export updates previously imported app & memory files IN PLACE (zero -import-2 clutter!)
    z1 = tmp_path / "export-v1.zip"
    with zipfile.ZipFile(z1, "w") as zf:
        zf.writestr("apps/demo-app/index.html", "<html>v1</html>")
        zf.writestr("notes/memory/prefs.md", "Pref v1")
    r_z1 = import_file(svc, z1, "export-v1.zip", opts)
    assert r_z1["apps_saved"] == 1
    assert (svc.workspace() / "apps" / "demo-app" / "index.html").read_text(encoding="utf-8") == "<html>v1</html>"

    z2 = tmp_path / "export-v2.zip"
    with zipfile.ZipFile(z2, "w") as zf:
        zf.writestr("apps/demo-app/index.html", "<html>v2-updated</html>")
        zf.writestr("notes/memory/prefs.md", "Pref v2 updated")
    r_z2 = import_file(svc, z2, "export-v2.zip", opts)
    assert r_z2["files_updated"] >= 2
    assert (svc.workspace() / "apps" / "demo-app" / "index.html").read_text(encoding="utf-8") == "<html>v2-updated</html>"
    assert not (svc.workspace() / "apps" / "demo-app" / "index-import-2.html").exists()

    # Verify memory_fts has ONLY the single updated entry (no stale duplicate row!)
    conn = sqlite3.connect(pdb)
    mem_rows = conn.execute("SELECT content FROM memory_fts WHERE path = 'memory/imports/prefs.md'").fetchall()
    conn.close()
    assert len(mem_rows) == 1
    assert mem_rows[0][0] == "Pref v2 updated"
