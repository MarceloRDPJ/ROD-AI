from datetime import datetime, timezone, timedelta

import pytest

from jarvis.core.events import Event
from jarvis.database.persistence import Persistence


def test_event_timestamp_is_timezone_aware_utc():
    event = Event(type="test.utc", source="test", payload={})
    parsed = datetime.fromisoformat(event.timestamp)

    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)


def test_get_events_by_type_filters_since():
    old_event = Event(
        type="telemetry.sample",
        source="test",
        payload={"watts": 1},
        timestamp=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
    )
    new_event = Event(type="telemetry.sample", source="test", payload={"value": 2})
    Persistence.log_event(old_event)
    Persistence.log_event(new_event)

    events = Persistence.get_events_by_type(
        "telemetry.sample",
        since=datetime.now(timezone.utc) - timedelta(days=1),
    )

    assert [event["payload"]["value"] for event in events] == [2]


@pytest.mark.asyncio
async def test_api_recent_events_reads_events_not_snapshots():
    from jarvis.api.app import get_recent_events

    Persistence.save_snapshot({"timestamp": datetime.now(timezone.utc).isoformat(), "fake": "snapshot"})
    event = Event(type="system.real", source="test", payload={"ok": True})
    Persistence.log_event(event)

    response = await get_recent_events(limit=10)

    assert len(response["events"]) == 1
    assert response["events"][0]["id"] == event.id
    assert "fake" not in response["events"][0]


@pytest.mark.asyncio
async def test_health_reports_local_skills_mode():
    from jarvis.api import app as api_app

    response = await api_app.get_system_health()

    assert response["checks"]["database"]["ok"] is True
    assert response["assistant"] == {
        "name": "ROD", "mode": "local_skills", "generative_ai": False
    }
    assert response["status"] == "ok"


@pytest.mark.asyncio
async def test_dashboard_endpoint_returns_payload(monkeypatch):
    from jarvis.api import app as api_app
    from jarvis.modules.system import SystemModule

    async def raw_status():
        return {"cpu": {"percent": 12}, "memory": {"percent": 43}, "temperature": 48}

    monkeypatch.setattr(SystemModule, "get_raw_status", raw_status)

    response = await api_app.get_dashboard_data()

    assert response is not None
    assert response.status_code == 200
