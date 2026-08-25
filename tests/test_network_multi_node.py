from types import SimpleNamespace

import pytest

from jarvis.config import Config
from jarvis.core.executor import Executor
from jarvis.services.poco_jobs import run_poco_job


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append(text)
        return SimpleNamespace(message_id=1)


@pytest.mark.asyncio
async def test_network_status_combines_pi_and_poco(monkeypatch):
    executor = Executor(SimpleNamespace(bot=FakeBot()))

    async def pi_metrics():
        return {"success": True, "latency_ms": 12.5}

    async def poco_job(action, timeout_seconds=70, params=None):
        return {"wifi_connected": True, "internet_validated": True}, None

    monkeypatch.setattr("jarvis.core.executor.NetworkModule.get_ping_metrics", pi_metrics)
    monkeypatch.setattr(executor, "_run_poco_job", poco_job)
    text = await executor._combined_network_status()
    assert "Pi: online — 12.5 ms" in text
    assert "Poco: Wi-Fi conectado e internet confirmada" in text
    assert "internet disponível nos dois pontos" in text


@pytest.mark.asyncio
async def test_speedtest_measures_pi_and_validates_poco(monkeypatch):
    executor = Executor(SimpleNamespace(bot=FakeBot()))

    async def pi_speed():
        return "🚀 *Velocidade da Internet:*\n\nDownload: 80 Mbit/s"

    async def poco_job(action, timeout_seconds=70, params=None):
        return {"wifi_connected": True, "internet_validated": True}, None

    monkeypatch.setattr("jarvis.core.executor.NetworkModule.run_speedtest", pi_speed)
    monkeypatch.setattr(executor, "_run_poco_job", poco_job)
    text = await executor._combined_speedtest()
    assert "Download: 80 Mbit/s" in text
    assert "Poco: Wi-Fi conectado e internet confirmada" in text
    assert "velocidade foi medida no Pi" in text


@pytest.mark.asyncio
async def test_speedtest_does_not_claim_poco_validation_when_it_times_out(monkeypatch):
    executor = Executor(SimpleNamespace(bot=FakeBot()))

    async def pi_speed():
        return "Download: 80 Mbit/s"

    async def poco_job(action, timeout_seconds=70, params=None):
        return None, "tempo esgotado"

    monkeypatch.setattr("jarvis.core.executor.NetworkModule.run_speedtest", pi_speed)
    monkeypatch.setattr(executor, "_run_poco_job", poco_job)
    text = await executor._combined_speedtest()
    assert "Poco: Não consegui validar" in text
    assert "não confirmou acesso" in text
    assert "O Poco validou o acesso" not in text


@pytest.mark.asyncio
async def test_active_job_is_allowed_with_a_stale_heartbeat(monkeypatch):
    completed = SimpleNamespace(
        status="completed",
        result={"wifi_connected": True, "internet_validated": True},
        error=None,
    )

    class Service:
        def status(self):
            return {"online": False, "heartbeat": {"received_at": 1}}

        def enqueue(self, action, params, ttl_seconds):
            return SimpleNamespace(job_id="job-1")

        def get_job(self, job_id):
            return completed

    import jarvis.api.app as api_app

    monkeypatch.setattr(Config, "POCO_NODE_ENABLED", True, raising=False)
    monkeypatch.setattr(api_app, "get_poco_service", lambda: Service())
    result, error = await run_poco_job("network_check", 5)
    assert error is None
    assert result["internet_validated"] is True
