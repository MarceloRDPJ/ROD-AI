from types import SimpleNamespace

import pytest

from jarvis.modules.system import SystemModule


def test_raw_status_reports_host_uptime_available_memory_swap_and_free_disk(monkeypatch):
    monkeypatch.setattr("jarvis.modules.system.time.time", lambda: 10_000.0)
    monkeypatch.setattr("jarvis.modules.system.psutil.boot_time", lambda: 1_000.0)
    monkeypatch.setattr("jarvis.modules.system.psutil.cpu_percent", lambda interval: 12.34)
    monkeypatch.setattr("jarvis.modules.system.psutil.cpu_count", lambda logical: 4)
    monkeypatch.setattr("jarvis.modules.system.psutil.getloadavg", lambda: (0.1, 0.2, 0.3))
    monkeypatch.setattr(
        "jarvis.modules.system.psutil.virtual_memory",
        lambda: SimpleNamespace(percent=70.55, used=700, available=300, total=1_000),
    )
    monkeypatch.setattr(
        "jarvis.modules.system.psutil.swap_memory",
        lambda: SimpleNamespace(percent=10.04, used=100, free=900, total=1_000),
    )
    monkeypatch.setattr(
        "jarvis.modules.system.psutil.disk_usage",
        lambda path: SimpleNamespace(percent=32.34, used=320, free=680, total=1_000),
    )
    monkeypatch.setattr(SystemModule, "_read_temperature", lambda: 45.1)
    monkeypatch.setattr(SystemModule, "_read_throttling", lambda: {"raw": "0x0", "active": []})

    status = SystemModule._get_raw_status_sync()

    assert status["uptime_seconds"] == 9_000
    assert status["service_uptime_seconds"] >= 0
    assert status["cpu_percent"] == 12.3
    assert status["memory"] == {"percent": 70.5, "used": 700, "available": 300, "total": 1_000}
    assert status["swap"]["free"] == 900
    assert status["disk"]["free"] == 680
    assert status["temperature_c"] == 45.1


def test_throttling_decoder_reports_current_and_historic_flags(monkeypatch):
    monkeypatch.setattr("jarvis.modules.system.shutil.which", lambda name: "/usr/bin/vcgencmd")
    monkeypatch.setattr(
        "jarvis.modules.system.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout="throttled=0x50005\n"),
    )

    result = SystemModule._read_throttling()

    assert result["active"] == ["undervoltage", "throttled"]
    assert result["occurred_since_boot"] == ["undervoltage", "throttled"]


@pytest.mark.asyncio
async def test_human_status_is_compact_and_labels_real_host_uptime(monkeypatch):
    async def raw():
        return {
            "cpu_percent": 6.9,
            "memory": {"percent": 70.5, "available": 288 * 1024 * 1024},
            "disk": {"percent": 32.3, "free": 75.9 * 1024 * 1024 * 1024},
            "temperature_c": 44.5,
            "uptime_seconds": 86_400,
            "throttling": {"raw": "0x0", "active": []},
        }

    monkeypatch.setattr(SystemModule, "get_raw_status", raw)
    text = await SystemModule.get_status()

    assert "CPU: 6.9%" in text
    assert "RAM: 70.5% (288 MiB livres)" in text
    assert "Temp: 44.5 °C" in text
    assert "Uptime do Pi: 1 day, 0:00:00" in text
