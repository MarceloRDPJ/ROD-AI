from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest, TelegramError

from jarvis import main
from jarvis.config import Config


def _update(user_id=123, chat_id=123, text="menu"):
    message = AsyncMock()
    message.text = text
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=chat_id),
        message=message,
    )


@pytest.mark.asyncio
async def test_start_rejects_other_user_and_greets_owner(monkeypatch):
    monkeypatch.setattr(Config, "ALLOWED_USER_ID", 123)
    denied = _update(user_id=999)
    allowed = _update()

    await main.start(denied, None)
    await main.start(allowed, None)

    denied.message.reply_text.assert_not_awaited()
    assert "ROD do Cerrado" in allowed.message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_help_command_preserves_inline_keyboard():
    update = _update()
    markup = object()
    executor = AsyncMock()
    executor.execute.return_value = {"text": "ajuda", "reply_markup": markup}
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"executor": executor}))

    await main.help_command(update, context)

    update.message.reply_text.assert_awaited_once_with("ajuda", reply_markup=markup)


@pytest.mark.asyncio
async def test_message_handler_routes_and_replies(monkeypatch):
    monkeypatch.setattr(Config, "ALLOWED_USER_ID", 123)
    monkeypatch.setattr(main.router, "route", AsyncMock(return_value={"intent": "help"}))
    executor = AsyncMock()
    executor.execute.return_value = "resposta real"
    context = SimpleNamespace(
        bot=AsyncMock(),
        application=SimpleNamespace(bot_data={"executor": executor}),
    )
    update = _update(text="menu")

    await main.handle_message(update, context)

    context.bot.send_chat_action.assert_awaited_once_with(123, "typing")
    update.message.reply_text.assert_awaited_once_with("resposta real", reply_markup=None)


@pytest.mark.asyncio
async def test_message_handler_rejects_unauthorized_user(monkeypatch):
    monkeypatch.setattr(Config, "ALLOWED_USER_ID", 123)
    context = SimpleNamespace(bot=AsyncMock(), application=SimpleNamespace(bot_data={}))
    update = _update(user_id=999)

    await main.handle_message(update, context)

    context.bot.send_chat_action.assert_not_awaited()
    update.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_message_handler_preserves_structured_response(monkeypatch):
    monkeypatch.setattr(Config, "ALLOWED_USER_ID", 123)
    monkeypatch.setattr(main.router, "route", AsyncMock(return_value={"intent": "help"}))
    markup = object()
    executor = AsyncMock()
    executor.execute.return_value = {"text": "menu estruturado", "reply_markup": markup}
    context = SimpleNamespace(bot=AsyncMock(), application=SimpleNamespace(bot_data={"executor": executor}))
    update = _update()

    await main.handle_message(update, context)

    update.message.reply_text.assert_awaited_once_with("menu estruturado", reply_markup=markup)


@pytest.mark.asyncio
async def test_message_handler_reports_internal_failure(monkeypatch):
    monkeypatch.setattr(Config, "ALLOWED_USER_ID", 123)
    monkeypatch.setattr(main.router, "route", AsyncMock(side_effect=RuntimeError("falha controlada")))
    context = SimpleNamespace(bot=AsyncMock(), application=SimpleNamespace(bot_data={}))
    update = _update()

    await main.handle_message(update, context)

    assert "falha controlada" in update.message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_callback_menu_edits_same_message(monkeypatch):
    monkeypatch.setattr(Config, "ALLOWED_USER_ID", 123)
    monkeypatch.setattr(main.router, "route", AsyncMock(return_value={"intent": "menu_rede"}))
    executor = AsyncMock()
    markup = object()
    executor.execute.return_value = {"text": "rede", "reply_markup": markup}
    query = AsyncMock()
    query.data = "menu_rede"
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=123),
    )
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"executor": executor}))

    await main.handle_callback(update, context)

    query.answer.assert_awaited_once()
    query.edit_message_text.assert_awaited_once_with(text="rede", reply_markup=markup)


@pytest.mark.asyncio
async def test_callback_falls_back_to_new_message_when_edit_fails(monkeypatch):
    monkeypatch.setattr(Config, "ALLOWED_USER_ID", 123)
    monkeypatch.setattr(main.router, "route", AsyncMock(return_value={"intent": "menu_rede"}))
    executor = AsyncMock()
    executor.execute.return_value = "rede atualizada"
    query = AsyncMock()
    query.data = "menu_rede"
    query.edit_message_text.side_effect = BadRequest("message not modified")
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=123),
    )
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"executor": executor}))

    await main.handle_callback(update, context)

    query.message.reply_text.assert_awaited_once_with("rede atualizada", reply_markup=None)


@pytest.mark.asyncio
async def test_callback_network_delegates_to_executor(monkeypatch):
    monkeypatch.setattr(Config, "ALLOWED_USER_ID", 123)
    executor = AsyncMock()
    query = AsyncMock()
    query.data = "net_ignore"
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=123),
    )
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"executor": executor}))

    await main.handle_callback(update, context)

    executor.handle_network_callback.assert_awaited_once_with(123, "net_ignore", query)


@pytest.mark.asyncio
async def test_safe_reply_retries_plain_text_after_bad_markdown():
    message = AsyncMock()
    message.reply_text.side_effect = [BadRequest("bad markdown"), None]

    await main.safe_reply_text(message, "**texto**", parse_mode="Markdown")

    assert message.reply_text.await_count == 2
    assert "parse_mode" not in message.reply_text.await_args.kwargs


@pytest.mark.asyncio
async def test_safe_reply_swallows_telegram_delivery_error():
    message = AsyncMock()
    message.reply_text.side_effect = TelegramError("offline")

    await main.safe_reply_text(message, "texto")

    message.reply_text.assert_awaited_once()


def test_long_messages_are_split_without_losing_content():
    chunks = main._message_chunks("a" * 25, limit=10)

    assert "".join(chunks) == "a" * 25
    assert all(len(chunk) <= 10 for chunk in chunks)
