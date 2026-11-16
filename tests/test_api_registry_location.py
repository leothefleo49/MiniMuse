"""Tests for API Registry (presets + dynamic AI API builder), multi-account Email/Google,
Local Calendar CRUD, Location & Travel/Traffic Store, and enhanced Goals/Tasks/Subtasks."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from nanomuse.calendar.feeds import CalendarFeeds
from nanomuse.calendar.local import LocalCalendarStore
from nanomuse.config import Settings
from nanomuse.connectors.accounts import EmailAccountsStore
from nanomuse.connectors.api_registry import (
    STANDARD_API_PRESETS,
    APIRegistry,
    parse_openapi_spec,
)
from nanomuse.connectors.google_oauth import GoogleOAuthManager, people_connections_to_vcf
from nanomuse.contacts import ContactBook
from nanomuse.goals import GoalStore
from nanomuse.llm import MockLLM
from nanomuse.location import LocationStore, build_map_links
from nanomuse.server import create_app
from nanomuse.server.service import MuseService
from nanomuse.tools.api_connector import ApiConnectorTool
from nanomuse.tools.calendar_tool import Calendar
from nanomuse.tools.goal_tools import Goals
from nanomuse.tools.location_tool import LocationTool
from nanomuse.vault.vault import CredentialVault


def _make(settings: Settings) -> tuple[TestClient, MuseService]:
    settings.server.token = "secret-token"
    service = MuseService(settings, llm=MockLLM([]))
    app = create_app(settings, service)
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer secret-token"
    return client, service


@pytest.fixture()
def server(settings: Settings) -> Iterator[tuple[TestClient, MuseService]]:
    client, service = _make(settings)
    with client:
        yield client, service


def test_standard_presets_have_dozens_of_integrations_and_multi_instance(tmp_path: Path) -> None:
    assert len(STANDARD_API_PRESETS) >= 35
    vault = CredentialVault(tmp_path / "vault.enc", tmp_path / "vault.key")
    reg = APIRegistry(tmp_path, vault=vault)

    gh1 = reg.upsert({"name": "Work GitHub", "preset_id": "github", "secret": "ghp_work_123"})
    gh2 = reg.upsert({"name": "Personal GitHub", "preset_id": "github", "secret": "ghp_pers_456"})
    assert gh1["id"] != gh2["id"]
    assert len(reg.list()) == 2
    assert vault.get(gh1["vault_key"]) == "ghp_work_123"
    assert vault.get(gh2["vault_key"]) == "ghp_pers_456"

    # Secrets are never exposed in public payloads
    public = json.dumps(gh1)
    assert "ghp_work_123" not in public

    # Arbitrary custom API + endpoint saving
    custom = reg.upsert(
        {
            "name": "Home Inventory",
            "base_url": "https://inventory.example.com/api",
            "auth_type": "api_key_header",
            "auth_header": "X-API-Key",
            "secret": "secret-inv-xyz",
            "headers": {"X-Custom": "1"},
            "ai_guide": "Custom REST service",
        }
    )
    reg.save_endpoint(
        custom["id"],
        {
            "name": "list_items",
            "method": "GET",
            "path": "/v1/items",
            "description": "Fetch items",
        },
    )
    reloaded = APIRegistry(tmp_path, vault=vault).get(custom["id"])
    assert reloaded is not None
    assert len(reloaded.endpoints) == 1
    assert reloaded.endpoints[0]["name"] == "list_items"


def test_openapi_parser_and_ssrf_guard() -> None:
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Todo API"},
        "paths": {
            "/tasks": {
                "get": {"summary": "List tasks"},
                "post": {"summary": "Create task"},
            },
            "/tasks/{id}": {
                "patch": {"summary": "Update task"},
            },
        },
    }
    parsed = parse_openapi_spec(spec)
    assert len(parsed["endpoints"]) == 3
    assert any(e["method"] == "POST" and e["path"] == "/tasks" for e in parsed["endpoints"])


async def test_api_registry_execute_and_tool(tmp_path: Path) -> None:
    vault = CredentialVault(tmp_path / "vault.enc", tmp_path / "vault.key")
    reg = APIRegistry(tmp_path, vault=vault)
    api_conn = reg.upsert(
        {
            "name": "Custom CRM",
            "base_url": "https://crm.example.com",
            "auth_type": "bearer",
            "secret": "topsecret-token",
        }
    )

    # SSRF blocks metadata IPs
    with pytest.raises(ValueError):
        await reg.execute(method="GET", url="http://169.254.169.254/latest")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.is_success = True
    mock_resp.url = "https://crm.example.com/deals"
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.json.return_value = {"items": [{"id": 1, "name": "Acme"}]}
    mock_resp.text = '{"items": [{"id": 1, "name": "Acme"}]}'

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.request = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_client):
        res = await reg.execute(api_id=api_conn["id"], method="GET", path="/deals")
        assert res["ok"] and res["status_code"] == 200
        sent_headers = mock_client.__aenter__.return_value.request.call_args.kwargs["headers"]
        assert sent_headers["Authorization"] == "Bearer topsecret-token"

    tool = ApiConnectorTool(registry=reg)
    assert tool.assess({"action": "list_presets"}).risk.value == "safe"
    assert tool.assess({"action": "request", "method": "POST", "path": "/deals"}).risk.value == "moderate"
    out = await tool.execute(action="list_presets", query="notion")
    assert "Notion" in out.output


def test_email_accounts_multi_and_legacy_migration(tmp_path: Path) -> None:
    vault = CredentialVault(tmp_path / "vault.enc", tmp_path / "vault.key")
    store = EmailAccountsStore(tmp_path, vault=vault)

    class LegacyEmail:
        enabled = True
        address = "legacy@example.com"
        imap_host = "imap.example.com"
        smtp_host = "smtp.example.com"
        password = "{{vault:EMAIL_PASSWORD}}"

    vault.set("EMAIL_PASSWORD", "pw-legacy")
    store.ensure_migrated_legacy(LegacyEmail())
    assert len(store.list()) == 1 and store.list()[0]["address"] == "legacy@example.com"

    second = store.upsert(
        {
            "label": "Work Gmail",
            "address": "work@gmail.com",
            "imap_host": "imap.gmail.com",
            "smtp_host": "smtp.gmail.com",
            "password": "app-pw-2",
        }
    )
    third = store.upsert(
        {
            "label": "Personal iCloud",
            "address": "me@icloud.com",
            "imap_host": "imap.mail.me.com",
            "smtp_host": "smtp.mail.me.com",
            "password": "app-pw-3",
        }
    )
    assert len(store.list()) == 3
    assert second["password_set"] and third["password_set"]
    assert store.remove(third["id"])
    assert len(store.list()) == 2


async def test_google_people_addresses_and_multi_account(tmp_path: Path) -> None:
    vcf = people_connections_to_vcf(
        [
            {
                "names": [{"displayName": "Sam Rivera"}],
                "emailAddresses": [{"value": "sam@example.com"}],
                "phoneNumbers": [{"value": "+1-555-0101"}],
                "addresses": [
                    {
                        "formattedValue": "742 Evergreen Terrace, Springfield, OR 97477",
                        "streetAddress": "742 Evergreen Terrace",
                        "city": "Springfield",
                        "region": "OR",
                        "postalCode": "97477",
                        "country": "USA",
                        "type": "home",
                    }
                ],
                "biographies": [{"value": "College friend #cycling"}],
            }
        ]
    )
    assert "FN:Sam Rivera" in vcf
    assert "ADR;TYPE=HOME:;;742 Evergreen Terrace;Springfield;OR;97477;USA" in vcf

    vault = CredentialVault(tmp_path / "vault.enc", tmp_path / "vault.key")
    mgr = GoogleOAuthManager(tmp_path, vault=vault)
    mgr._write_store(
        {
            "primary_email": "one@gmail.com",
            "accounts": {
                "one@gmail.com": {
                    "email": "one@gmail.com",
                    "access_token": "tok1",
                    "refresh_token": "ref1",
                    "expires_at": 9999999999,
                    "scopes": ["openid"],
                },
                "two@gmail.com": {
                    "email": "two@gmail.com",
                    "access_token": "tok2",
                    "refresh_token": "ref2",
                    "expires_at": 9999999999,
                    "scopes": ["openid"],
                },
            },
        }
    )
    st = mgr.status()
    assert st["signed_in"]
    assert len(st["accounts"]) == 2
    assert {a["email"] for a in st["accounts"]} == {"one@gmail.com", "two@gmail.com"}

    st_after = await mgr.logout("one@gmail.com")
    assert st_after["signed_in"] and len(st_after["accounts"]) == 1 and st_after["email"] == "two@gmail.com"


async def test_location_store_search_contacts_trips_and_map_links(tmp_path: Path, settings: Settings) -> None:
    store = LocationStore(tmp_path / "location.db")
    home = store.add_place(
        name="Home",
        address="100 Main St, Boulder, CO",
        lat=40.015,
        lng=-105.2705,
        category="home",
        tags=["#home", "base"],
        context="Blue house, park in driveway",
        favorite=True,
    )
    gym = store.add_place(
        name="Boulder Rock Club",
        address="2829 Mapleton Ave, Boulder, CO",
        lat=40.025,
        lng=-105.255,
        category="frequent",
        tags=["climbing", "fitness"],
        visit_count=12,
    )
    trip = store.log_trip(
        origin_name="Home",
        origin_address=home["address"],
        place_id=gym["id"],
        place_name=gym["name"],
        address=gym["address"],
        lat=gym["lat"],
        lng=gym["lng"],
        distance_mi=2.4,
        duration_min=8,
        mode="driving",
        traffic_level="light",
        notes="Smooth morning drive",
    )
    assert trip["distance_mi"] == 2.4
    assert trip["avg_speed_mph"] == 18.0

    book = ContactBook(settings.connectors.contacts, own_file=tmp_path / "own_contacts.json")
    book.add("Maya Patel", note="Friend from work #coffee", address="123 Pearl St, Boulder, CO")

    by_tag = store.search("", contacts=book.contacts, tag="#climbing")
    assert len(by_tag["places"]) == 1 and by_tag["places"][0]["name"] == "Boulder Rock Club"

    by_contact = store.search("Maya", contacts=book.contacts)
    assert len(by_contact["contacts"]) == 1
    assert "google_maps" in by_contact["contacts"][0]["map_links"]
    assert by_contact["contacts"][0]["map_links"]["waze"].startswith("https://waze.com/")

    links = build_map_links("123 Pearl St, Boulder, CO", label="Maya")
    assert "google.com/maps" in links["google_maps"]
    assert "maps.apple.com" in links["apple_maps"]

    tool = LocationTool(store=store, contacts=book)
    res = await tool.execute(action="where_is", query="Maya")
    assert "123 Pearl St" in res.output


async def test_local_calendar_crud_and_goals_tasks_subtasks(tmp_path: Path, settings: Settings) -> None:
    local_cal = LocalCalendarStore(tmp_path / "cal-local.json")
    ev = local_cal.create(
        summary="Dentist",
        start="2026-12-01T14:00:00",
        end="2026-12-01T15:00:00",
        location="400 Broadway",
        tags=["#health"],
    )
    assert ev["uid"] in [e["uid"] for e in local_cal.list()]
    updated_ev = local_cal.update(ev["uid"], {"summary": "Dentist Checkup"})
    assert updated_ev is not None and updated_ev["summary"] == "Dentist Checkup"

    feeds = CalendarFeeds(settings.connectors.calendar, local_store=local_cal)
    assert feeds.configured
    tool = Calendar(feeds=feeds, workspace=tmp_path)
    out = await tool.execute(
        action="create",
        title="Team Sync",
        start="2026-12-01 16:00",
        end="2026-12-01 16:30",
        location="HQ Room 2",
        tags=["#work"],
    )
    assert "Team Sync" in out.output
    assert len(local_cal.list()) == 2

    gstore = GoalStore(tmp_path / "goals.db")
    low = gstore.create("Organize closet", kind="task", priority="low", tags=["#home"])
    urgent = gstore.create(
        "File taxes",
        kind="task",
        priority="urgent",
        tags=["finance", "#deadline"],
        due="2026-04-15",
        due_time="17:00",
        recurrence="yearly",
        steps=["Gather W-2", "Review deductions", "Submit return"],
    )
    sorted_items = gstore.list(sort="priority")
    assert sorted_items[0].id == urgent.id
    assert sorted_items[1].id == low.id

    gstore.reorder_steps(urgent.id, [3, 1, 2])
    reordered = gstore.get(urgent.id)
    assert reordered is not None
    assert [s.title for s in reordered.steps] == ["Submit return", "Gather W-2", "Review deductions"]

    gstore.delete_step(urgent.id, 1)
    after_del = gstore.get(urgent.id)
    assert after_del is not None and len(after_del.steps) == 2

    gtool = Goals(store=gstore)
    tout = await gtool.execute(action="list", kind="task", tag="#home")
    assert "Organize closet" in tout.output and "File taxes" not in tout.output


def test_api_routes_for_new_features(server: tuple[TestClient, MuseService]) -> None:
    client, _svc = server

    # Presets + APIs
    r = client.get("/api/connections/presets")
    assert r.status_code == 200 and r.json()["count"] >= 35

    r = client.post(
        "/api/connections/apis",
        json={
            "name": "Linear Workspace",
            "preset_id": "linear",
            "secret": "lin_api_secret",
            "endpoints": [{"name": "viewer", "method": "POST", "path": "/graphql"}],
        },
    )
    assert r.status_code == 200
    api_id = r.json()["id"]
    assert r.json()["has_secret"] is True

    # Multi-account Email
    r = client.post(
        "/api/connections/email/accounts",
        json={
            "label": "Work",
            "address": "work@example.com",
            "imap_host": "imap.example.com",
            "smtp_host": "smtp.example.com",
            "password": "secret-email-pw",
        },
    )
    assert r.status_code == 200
    assert len(r.json()) == 1

    # Calendar CRUD
    r = client.post(
        "/api/calendar/events",
        json={"summary": "Lunch with Alex", "start": "2026-11-20T12:00:00", "location": "Cafe Bistro", "tags": ["social"]},
    )
    assert r.status_code == 200
    uid = r.json()["uid"]
    r = client.patch(f"/api/calendar/events/{uid}", json={"summary": "Lunch with Alex & Sam"})
    assert r.status_code == 200 and r.json()["summary"] == "Lunch with Alex & Sam"

    # Location CRUD + Trip + Search
    r = client.post(
        "/api/location/places",
        json={"name": "Mom's House", "address": "55 Oak Ave, Denver, CO", "category": "family", "tags": ["family", "Mom"]},
    )
    assert r.status_code == 200
    pid = r.json()["id"]
    r = client.post(f"/api/location/places/{pid}/visit")
    assert r.status_code == 200 and r.json()["visit_count"] == 2
    r = client.post(
        "/api/location/trips",
        json={"origin_name": "Home", "place_name": "Mom's House", "distance_mi": 25.0, "duration_min": 30.0, "mode": "driving", "traffic_level": "light"},
    )
    assert r.status_code == 200 and r.json()["avg_speed_mph"] == 50.0
    r = client.get("/api/location/search", params={"tag": "#family"})
    assert r.status_code == 200 and len(r.json()["places"]) == 1

    # Goals/Tasks enhanced CRUD
    r = client.post(
        "/api/goals",
        json={
            "title": "Plan roadtrip",
            "kind": "task",
            "priority": "high",
            "tags": ["travel"],
            "due": "2026-11-25",
            "due_time": "09:00",
            "steps": ["Check tires", "Pack snacks"],
        },
    )
    assert r.status_code == 200
    gid = r.json()["id"]
    assert r.json()["kind"] == "task" and r.json()["priority"] == "high" and "#travel" in r.json()["tags"]
    r = client.post(f"/api/goals/{gid}/steps/reorder", json={"order": [2, 1]})
    assert r.status_code == 200 and [s["title"] for s in r.json()["steps"]] == ["Pack snacks", "Check tires"]
    r = client.delete(f"/api/goals/{gid}/steps/1")
    assert r.status_code == 200 and len(r.json()["steps"]) == 1

    # Cleanup
    assert client.delete(f"/api/connections/apis/{api_id}").status_code == 200
    assert client.delete(f"/api/calendar/events/{uid}").status_code == 200


def test_mobile_header_scrim_is_transparent_not_solid_bar() -> None:
    root = Path(__file__).resolve().parent.parent
    css = (root / "web" / "src" / "index.css").read_text(encoding="utf-8")
    assert ".header-scrim" in css
    block = css.split(".header-scrim {", 1)[1].split("}", 1)[0]
    assert "background: transparent" in block
    assert "pointer-events: none" in block

    header_tsx = (root / "web" / "src" / "components" / "MuseHeader.tsx").read_text(encoding="utf-8")
    assert "bg-transparent" in header_tsx

    onboarding_tsx = (root / "web" / "src" / "screens" / "Onboarding.tsx").read_text(encoding="utf-8")
    assert "min-h-full flex-col items-center justify-start" in onboarding_tsx

