"""The guardian must not confuse Android sleep with a disconnected Poco."""

import pytest

from jarvis.services.guardian import GuardianService


class FakePocoService:
    def __init__(self, status):
        self._status = status

    def status(self):
        return self._status


def build_guardian(monkeypatch, status, enabled=True):
    sent = []

    class FakeBot:
        async def send_message(self, chat_id, text):
            sent.append(text)

    guardian = GuardianService(type("App", (), {"bot": FakeBot()})(), chat_id=1)

    from jarvis.config import Config
    import jarvis.api.app as api_app

    monkeypatch.setattr(Config, "POCO_NODE_ENABLED", enabled, raising=False)
    monkeypatch.setattr(api_app, "get_poco_service", lambda: FakePocoService(status))
    return guardian, sent


@pytest.mark.asyncio
async def test_stale_heartbeat_is_telemetry_not_a_false_offline_alert(monkeypatch):
    status = {"online": False, "heartbeat": {"node_id": "poco", "received_at": 1}}
    guardian, sent = build_guardian(monkeypatch, status)
    for _ in range(8):
        await guardian.check_poco_node()
    assert sent == []
    assert guardian.poco_state == "delayed"


@pytest.mark.asyncio
async def test_recovery_from_delayed_heartbeat_is_silent(monkeypatch):
    status = {"online": False, "heartbeat": {"node_id": "poco", "received_at": 1}}
    guardian, sent = build_guardian(monkeypatch, status)
    await guardian.check_poco_node()
    status["online"] = True
    status["heartbeat"] = {"node_id": "poco", "received_at": 2, "pending_results": 2}
    await guardian.check_poco_node()
    assert sent == []
    assert guardian.poco_state == "online"


@pytest.mark.asyncio
async def test_never_alerts_for_a_node_that_never_reported(monkeypatch):
    guardian, sent = build_guardian(monkeypatch, {"online": False, "heartbeat": None})
    for _ in range(5):
        await guardian.check_poco_node()
    assert sent == []
    assert guardian.poco_state == "unknown"


@pytest.mark.asyncio
async def test_disabled_node_is_not_monitored(monkeypatch):
    status = {"online": False, "heartbeat": {"node_id": "poco", "received_at": 1}}
    guardian, sent = build_guardian(monkeypatch, status, enabled=False)
    for _ in range(5):
        await guardian.check_poco_node()
    assert sent == []


@pytest.mark.asyncio
async def test_pi_ping_failure_is_suppressed_when_poco_confirms_internet(monkeypatch):
    guardian, sent = build_guardian(
        monkeypatch,
        {"online": True, "heartbeat": {"node_id": "poco", "received_at": 1}},
    )

    async def pi_offline():
        return {"success": False, "latency_ms": None}

    async def poco_online():
        return {"internet_validated": True, "wifi_connected": True}, None

    monkeypatch.setattr("jarvis.services.guardian.NetworkModule.get_ping_metrics", pi_offline)
    monkeypatch.setattr(guardian, "_active_poco_internet_probe", poco_online)
    await guardian.check_internet_status()
    await guardian.check_internet_status()
    assert sent == []
    assert guardian.internet_state == "online"
    assert guardian.consecutive_ping_failures == 0


@pytest.mark.asyncio
async def test_outage_is_reported_once_when_pi_and_poco_both_fail(monkeypatch):
    guardian, sent = build_guardian(
        monkeypatch,
        {"online": True, "heartbeat": {"node_id": "poco", "received_at": 1}},
    )

    async def pi_offline():
        return {"success": False, "latency_ms": None}

    async def poco_offline():
        return {"internet_validated": False, "wifi_connected": True}, None

    monkeypatch.setattr("jarvis.services.guardian.NetworkModule.get_ping_metrics", pi_offline)
    monkeypatch.setattr(guardian, "_active_poco_internet_probe", poco_offline)
    for _ in range(5):
        await guardian.check_internet_status()
    assert len(sent) == 1
    assert "Pi nem no Poco" in sent[0]
    assert guardian.internet_state == "offline"
