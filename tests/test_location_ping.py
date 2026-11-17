"""A GPS position sample is not a journey: it must be stored as a sample so travel stats stay honest."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nanomuse.config import Settings
from nanomuse.llm import MockLLM
from nanomuse.server import create_app
from nanomuse.server.service import MuseService


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[tuple[TestClient, dict[str, str]]]:
    s = Settings(data_dir=tmp_path / "data", workspace=tmp_path / "ws")
    svc = MuseService(s, llm=MockLLM([]))
    with TestClient(create_app(settings=s, service=svc)) as c:
        yield c, {"Authorization": f"Bearer {svc.token}"}


def trips(c: TestClient, h: dict[str, str]) -> list[dict]:
    return c.get("/api/location", headers=h).json()["trips"]


def test_a_gps_ping_is_stored_as_a_position_sample(client) -> None:  # noqa: ANN001
    c, h = client
    c.post("/api/location/ping", headers=h, json={"lat": 40.76, "lng": -111.89, "place_name": "GPS Snapshot"})
    (sample,) = trips(c, h)
    assert sample["source"] == "gps_ping"
    assert (sample["lat"], sample["lng"]) == (40.76, -111.89)


def test_a_trip_the_person_logs_stays_a_trip(client) -> None:  # noqa: ANN001
    c, h = client
    c.post("/api/location/trips", headers=h, json={"origin_name": "Home", "place_name": "Work", "distance_mi": 10})
    (trip,) = trips(c, h)
    assert trip["source"] == "manual"


def test_a_source_that_is_given_is_kept(client) -> None:  # noqa: ANN001
    c, h = client
    c.post("/api/location/ping", headers=h, json={"lat": 1.0, "lng": 2.0, "source": "watch"})
    (sample,) = trips(c, h)
    assert sample["source"] == "watch"
