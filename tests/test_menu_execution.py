from unittest.mock import AsyncMock

import pytest

from jarvis.config import Config
from jarvis.core.executor import Executor
from jarvis.modules.adguard import AdGuardClient
from jarvis.modules.network import NetworkModule
from jarvis.services.device_intelligence import DeviceIntelligence


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intent", "expected_callback"),
    [
        ("help", "menu_rede"),
        ("menu_rede", "quem ta na rede"),
        ("menu_agenda", "listar lembretes"),
        ("menu_automacoes", "listar automacoes"),
        ("menu_sistema", "status do sistema"),
    ],
)
async def test_every_menu_renders_working_buttons(monkeypatch, intent, expected_callback):
    monkeypatch.setattr(Config, "ALLOWED_USER_ID", 123)
    executor = Executor(AsyncMock())

    response = await executor.execute({"intent": intent, "action": "show", "params": {}}, 123)

    callbacks = [button.callback_data for row in response["reply_markup"].inline_keyboard for button in row]
    assert expected_callback in callbacks
    assert response["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intent",
    ["identity_who", "identity_creator", "identity_purpose", "identity_capabilities", "identity_tech", "greet"],
)
async def test_identity_and_greeting_intents_return_language(monkeypatch, intent):
    monkeypatch.setattr(Config, "ALLOWED_USER_ID", 123)
    response = await Executor(AsyncMock()).execute({"intent": intent, "params": {}}, 123)

    assert isinstance(response, str)
    assert response.strip()


@pytest.mark.asyncio
async def test_network_stats_labels_dns_queries_not_bandwidth(monkeypatch):
    monkeypatch.setattr(Config, "ALLOWED_USER_ID", 123)
    monkeypatch.setattr(AdGuardClient, "get_stats", AsyncMock(return_value={"num_dns_queries": 12, "num_blocked_filtering": 2}))
    monkeypatch.setattr(AdGuardClient, "get_top_clients", AsyncMock(return_value=[{"name": "TV", "ip": "192.168.1.20", "queries": 7}]))

    response = await Executor(AsyncMock()).execute({"intent": "network_stats", "params": {}}, 123)

    assert "DNS Queries: 12" in response
    assert "7 queries" in response


@pytest.mark.asyncio
async def test_device_details_intent_uses_live_mac_and_dossier(monkeypatch):
    monkeypatch.setattr(Config, "ALLOWED_USER_ID", 123)
    monkeypatch.setattr(NetworkModule, "resolve_mac_by_ip", AsyncMock(return_value="00:11:22:33:44:55"))
    monkeypatch.setattr(DeviceIntelligence, "dossier", AsyncMock(return_value={"profile": {}}))
    monkeypatch.setattr(DeviceIntelligence, "format_dossier", lambda data: "dossie pronto")

    response = await Executor(AsyncMock()).execute(
        {"intent": "network_device_details", "params": {"ip": "192.168.1.20"}},
        123,
    )

    assert response == "dossie pronto"


@pytest.mark.asyncio
async def test_network_info_callback_renders_dossier(monkeypatch):
    monkeypatch.setattr(DeviceIntelligence, "dossier", AsyncMock(return_value={"profile": {}}))
    monkeypatch.setattr(DeviceIntelligence, "format_dossier", lambda data: "dossie do botao")
    query = AsyncMock()

    await Executor(AsyncMock()).handle_network_callback(
        123,
        "net_info_192.168.1.20_00:11:22:33:44:55",
        query,
    )

    query.edit_message_text.assert_awaited_once_with("dossie do botao")
