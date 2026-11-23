from unittest.mock import AsyncMock

import pytest

from jarvis.core.context_reader import ContextReader
from jarvis.database.persistence import Persistence
from jarvis.modules.network import NetworkModule
from jarvis.modules.system import SystemModule
from jarvis.services.device_intelligence import DeviceIntelligence
from jarvis.services.guardian import GuardianService


@pytest.mark.asyncio
async def test_guardian_keeps_baseline_when_scan_is_empty(monkeypatch):
    monkeypatch.setattr(NetworkModule, "get_raw_snapshot", AsyncMock(return_value={"devices": []}))
    guardian = GuardianService(AsyncMock(), 123)
    guardian.online_macs = {"00:11:22:33:44:55"}

    await guardian.check_device_changes()

    assert guardian.online_macs == {"00:11:22:33:44:55"}


@pytest.mark.asyncio
async def test_guardian_baseline_records_ip_without_alert(monkeypatch):
    device = {"ip": "192.168.1.20", "mac": "00:11:22:33:44:55"}
    monkeypatch.setattr(NetworkModule, "get_raw_snapshot", AsyncMock(return_value={"devices": [device]}))
    app = AsyncMock()
    guardian = GuardianService(app, 123)

    await guardian.check_device_changes()

    assert Persistence.get_device_profile(device["mac"])["last_ip"] == device["ip"]
    app.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_guardian_detects_join_and_updates_devices_that_stayed(monkeypatch):
    first = {"ip": "192.168.1.20", "mac": "00:11:22:33:44:55"}
    joined = {"ip": "192.168.1.21", "mac": "02:11:22:33:44:66"}
    monkeypatch.setattr(
        NetworkModule,
        "get_raw_snapshot",
        AsyncMock(side_effect=[{"devices": [first]}, {"devices": [first, joined]}]),
    )
    monkeypatch.setattr(
        DeviceIntelligence,
        "dossier",
        AsyncMock(return_value={
            "identity": {"ip": joined["ip"], "mac": joined["mac"]},
            "profile": {
                "mac": joined["mac"], "last_ip": joined["ip"], "seen_count": 1,
                "first_seen": None, "last_seen": None, "ips": [], "mac_is_private": True,
            },
            "activity": {"available": True, "dns_queries_sampled": 0, "blocked_sampled": 0, "top_domains": []},
        }),
    )
    app = AsyncMock()
    guardian = GuardianService(app, 123)

    await guardian.check_device_changes()
    await guardian.check_device_changes()

    app.bot.send_message.assert_awaited_once()
    assert Persistence.get_device_profile(first["mac"])["seen_count"] == 2


@pytest.mark.asyncio
async def test_guardian_reports_recovery_and_queued_power_message(monkeypatch):
    monkeypatch.setattr(
        NetworkModule,
        "get_ping_metrics",
        AsyncMock(return_value={"success": True, "latency_ms": 12.5}),
    )
    guardian = GuardianService(AsyncMock(), 123)
    guardian.internet_state = "offline"
    guardian.pending_power_msg = "Energia voltou"
    guardian.send_message = AsyncMock(return_value=True)

    await guardian.check_internet_status()

    assert guardian.internet_state == "online"
    assert guardian.pending_power_msg is None
    assert guardian.send_message.await_count == 2


@pytest.mark.asyncio
async def test_guardian_requires_three_failures_before_offline_alert(monkeypatch):
    monkeypatch.setattr(NetworkModule, "get_ping_metrics", AsyncMock(return_value={"success": False}))
    guardian = GuardianService(AsyncMock(), 123)
    guardian.send_message = AsyncMock(return_value=True)

    await guardian.check_internet_status()
    await guardian.check_internet_status()
    await guardian.check_internet_status()

    assert guardian.internet_state == "offline"
    guardian.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_power_gap_is_queued_until_connectivity_returns(monkeypatch):
    monkeypatch.setattr(
        ContextReader,
        "detect_gap",
        lambda max_gap_minutes: {"status": "ok", "exceeded": True, "gap_minutes": 12},
    )
    # Uptime do host menor que o intervalo: o Pi reiniciou de fato, então a
    # queda de energia é uma causa comprovada e não uma suposição.
    monkeypatch.setattr(
        SystemModule, "get_raw_status", AsyncMock(return_value={"uptime_seconds": 90})
    )
    guardian = GuardianService(AsyncMock(), 123)

    await guardian.check_power_status()

    assert "12 minutos" in guardian.pending_power_msg


@pytest.mark.parametrize(
    ("vendor", "ports", "hostname", "expected"),
    [
        ("Apple, Inc.", [], "iphone-de-marcelo", "iPhone"),
        ("Raspberry Pi Foundation", [], "", "Raspberry Pi"),
        ("", [22], "", "Servidor Linux/SSH"),
        ("", [53], "", "Roteador/DNS"),
        ("Espressif", [], "", "Smart Plug/Lâmpada (IoT)"),
    ],
)
def test_device_type_uses_conservative_observable_hints(vendor, ports, hostname, expected):
    assert NetworkModule._guess_device_type(vendor, ports, hostname) == expected


@pytest.mark.asyncio
async def test_private_mac_identity_skips_vendor_lookup(monkeypatch):
    monkeypatch.setattr(NetworkModule, "_resolve_hostname_deep", AsyncMock(return_value="iphone.local"))

    identity = await NetworkModule.identify_device("192.168.1.20", "02:11:22:33:44:55")

    assert identity["mac_is_private"] is True
    assert identity["vendor"] is None
    assert identity["hostname"] == "iphone.local"


def test_dossier_date_keeps_unexpected_legacy_value_visible():
    assert DeviceIntelligence._date("data-legada") == "data-legada"
