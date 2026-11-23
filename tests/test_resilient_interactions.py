from unittest.mock import AsyncMock

import pytest

from jarvis.config import Config
from jarvis.core.context import ContextEngine
from jarvis.core.executor import Executor
from jarvis.core.flows import RemindersFlow
from jarvis.core.router import route
from jarvis.modules.adguard import AdGuardClient
from jarvis.nlp.entities import extract_structured_entities
from jarvis.services.guardian import GuardianService
from jarvis.database.persistence import Persistence
from jarvis.modules.network import NetworkModule


def test_structured_entities_survive_natural_language_normalization():
    entities = extract_structured_entities(
        "Bloquear 192.168.1.77 e o site https://www.youtube.com/watch?v=1"
    )

    assert entities["ip"] == "192.168.1.77"
    assert entities["domain"] == "youtube.com"


def test_structured_entities_reject_invalid_ip():
    entities = extract_structured_entities("bloquear 999.168.1.77")

    assert entities["ip"] is None


@pytest.mark.asyncio
async def test_explicit_ip_block_is_deterministic_and_confirmed():
    result = await route("bloquear 192.168.1.77", chat_id=123)

    assert result["intent"] == "network_block_device"
    assert result["params"]["ip"] == "192.168.1.77"
    assert result["requires_confirmation"] is True
    assert result["source"] == "rule"


@pytest.mark.asyncio
async def test_explicit_domain_block_preserves_domain():
    result = await route("bloquear https://youtube.com/watch?v=1", chat_id=123)

    assert result["intent"] == "network_block_site"
    assert result["params"]["domain"] == "youtube.com"
    assert result["requires_confirmation"] is True


@pytest.mark.asyncio
async def test_pronoun_resolves_only_recent_network_target():
    ContextEngine.set_network_target(123, "192.168.1.77", "aa:bb:cc:dd:ee:ff")

    result = await route("bloquear ele", chat_id=123)

    assert result["intent"] == "network_block_device"
    assert result["params"]["ip"] == "192.168.1.77"
    assert result["requires_confirmation"] is True


@pytest.mark.asyncio
async def test_pronoun_without_context_never_becomes_block_action():
    result = await route("bloquear ele", chat_id=123)

    assert result["intent"] != "network_block_device"


@pytest.mark.asyncio
async def test_rename_uses_ip_from_original_text():
    result = await route("renomear 192.168.1.77 para TV Sala", chat_id=123)

    assert result["intent"] == "network_rename"
    assert result["params"]["target"] == "192.168.1.77"
    assert result["params"]["name"] == "TV Sala"


@pytest.mark.asyncio
async def test_sensitive_confirmation_survives_executor_restart(monkeypatch):
    monkeypatch.setattr(Config, "ALLOWED_USER_ID", 123)
    block_client = AsyncMock(return_value={"success": True, "message": "ok"})
    monkeypatch.setattr(AdGuardClient, "block_client", block_client)

    command = await route("bloquear 192.168.1.77", chat_id=123)
    first_executor = Executor(application=None)
    prompt = await first_executor.execute(command, 123)

    assert "192.168.1.77" in prompt["text"]
    assert ContextEngine.get_pending_action(123) is not None

    restarted_executor = Executor(application=None)
    response = await restarted_executor.execute(
        {"intent": "action_confirm", "action": "confirm", "params": {}},
        123,
    )

    assert "bloqueado" in response
    block_client.assert_awaited_once_with("192.168.1.77")
    assert ContextEngine.get_pending_action(123) is None


@pytest.mark.asyncio
async def test_unknown_request_returns_actionable_clarification(monkeypatch):
    monkeypatch.setattr(Config, "ALLOWED_USER_ID", 123)
    executor = Executor(application=None)
    intent = await route("batata com banana?", chat_id=123)

    response = await executor.execute(intent, 123)

    callbacks = [button.callback_data for row in response["reply_markup"].inline_keyboard for button in row]
    assert {"menu_sistema", "menu_rede", "menu_agenda", "help"}.issubset(callbacks)


def test_reminder_confirmation_survives_unrelated_reply():
    ContextEngine.save_context(
        123,
        {
            "flow": {
                "type": "reminder_creation",
                "step": "confirmation",
                "data": {"text": "tirar o lixo", "target_date": "2026-11-24T18:00:00-03:00"},
            }
        },
    )

    response = RemindersFlow.handle_response(123, "qual horário mesmo?", ContextEngine.get_context(123))

    assert "esperando sua decisão" in response
    assert ContextEngine.get_context(123)["flow"]["step"] == "confirmation"

    decorated = Executor._decorate_reminder_flow_response(123, response)
    callbacks = [button.callback_data for row in decorated["reply_markup"].inline_keyboard for button in row]
    assert callbacks == ["sim", "nao"]


@pytest.mark.asyncio
async def test_guardian_alert_provides_actions_and_context():
    app = AsyncMock()
    guardian = GuardianService(app, chat_id=123)
    device = {"ip": "192.168.1.77", "mac": "aa:bb:cc:dd:ee:ff"}

    await guardian.handle_device_joined(device["mac"], [device])

    sent = app.bot.send_message.await_args.kwargs
    callbacks = [button.callback_data for row in sent["reply_markup"].inline_keyboard for button in row]
    assert "net_info_192.168.1.77_aa:bb:cc:dd:ee:ff" in callbacks
    assert "net_block_192.168.1.77" in callbacks
    assert "net_reg_192.168.1.77_aa:bb:cc:dd:ee:ff" in callbacks
    assert ContextEngine.get_recent_network_target(123)["ip"] == "192.168.1.77"


def test_device_history_preserves_name_and_ip_changes():
    mac = "00:11:22:33:44:55"
    Persistence.register_device_seen(mac, ip="192.168.1.20", vendor="Example")
    Persistence.set_device_name(mac, "Geladeira da cozinha")
    Persistence.register_device_seen(mac, ip="192.168.1.21", hostname="fridge.local")

    profile = Persistence.get_device_profile(mac)

    assert profile["name"] == "Geladeira da cozinha"
    assert profile["seen_count"] == 2
    assert profile["last_ip"] == "192.168.1.21"
    assert {item["ip"] for item in profile["ips"]} == {"192.168.1.20", "192.168.1.21"}


def test_private_mac_is_not_treated_as_vendor_oui():
    assert NetworkModule.is_private_mac("02:11:22:33:44:55") is True
    assert NetworkModule.is_private_mac("00:11:22:33:44:55") is False


@pytest.mark.asyncio
async def test_adguard_activity_is_dns_not_bandwidth(monkeypatch):
    monkeypatch.setattr(
        AdGuardClient,
        "_request",
        AsyncMock(return_value=(200, {"data": [
            {"client": "192.168.1.20", "question": {"name": "example.com"}, "reason": "NotFilteredNotFound", "time": "2026-01-01T10:00:00Z"},
            {"client": "192.168.1.20", "question": {"name": "example.com"}, "reason": "FilteredBlackList", "time": "2026-01-01T10:01:00Z"},
            {"client": "192.168.1.99", "question": {"name": "other.test"}, "reason": "NotFilteredNotFound"},
        ]})),
    )

    activity = await AdGuardClient.get_client_activity("192.168.1.20")

    assert activity["dns_queries_sampled"] == 2
    assert activity["blocked_sampled"] == 1
    assert activity["top_domains"] == ["example.com"]
    assert activity["is_bandwidth"] is False


@pytest.mark.asyncio
async def test_device_details_command_is_safe_read_only_query():
    result = await route("investigar 192.168.1.77", chat_id=123)

    assert result["intent"] == "network_device_details"
    assert result["params"]["ip"] == "192.168.1.77"
    assert result.get("requires_confirmation") is not True


@pytest.mark.asyncio
async def test_network_block_button_interrupts_old_flow(monkeypatch):
    monkeypatch.setattr(Config, "ALLOWED_USER_ID", 123)
    ContextEngine.save_context(123, {"flow": {"type": "reminder_creation", "step": "confirmation"}})
    query = AsyncMock()
    executor = Executor(application=None)

    await executor.handle_network_callback(123, "net_block_192.168.1.77", query)

    assert ContextEngine.get_context(123)["flow"] is None
    assert ContextEngine.get_pending_action(123)["action"]["params"]["ip"] == "192.168.1.77"
    query.edit_message_text.assert_awaited_once()
