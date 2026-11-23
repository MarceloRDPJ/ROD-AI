from unittest.mock import AsyncMock

import pytest

from jarvis.core.telegram_safe import (
    plain_retry_text,
    safe_reply_text,
    sanitize_inline_value,
)


@pytest.mark.asyncio
async def test_safe_reply_text_renders_markup_by_default():
    message = AsyncMock()

    await safe_reply_text(message, "LLM local: disponível\nEndpoint/CLI: /tmp/model.gguf")

    message.reply_text.assert_called_once()
    assert message.reply_text.call_args.kwargs["parse_mode"] == "Markdown"


@pytest.mark.asyncio
async def test_safe_reply_text_retries_without_parse_mode_on_bad_request():
    message = AsyncMock()
    message.reply_text.side_effect = [Exception("Can't parse entities"), None]

    await safe_reply_text(message, "texto *quebrado*", parse_mode="Markdown")

    assert message.reply_text.call_count == 2
    assert "parse_mode" not in message.reply_text.call_args_list[1].kwargs


@pytest.mark.asyncio
async def test_safe_reply_text_honours_explicit_plain_text():
    message = AsyncMock()

    await safe_reply_text(message, "texto cru", parse_mode=None)

    assert "parse_mode" not in message.reply_text.call_args.kwargs


def test_plain_fallback_strips_markup_but_keeps_data():
    """O fallback antes removia ponto e hifen, destruindo IP, data e hora."""
    cleaned = plain_retry_text("IP `192.168.1.152` em 23/11/2026 10:13 - *ok*")

    assert "192.168.1.152" in cleaned
    assert "23/11/2026 10:13" in cleaned
    assert "`" not in cleaned
    assert "*" not in cleaned


def test_sanitize_inline_value_neutralises_hostname_markup():
    """Hostname vem do proprio aparelho e nao pode desformatar a mensagem."""
    assert sanitize_inline_value("tv_*sala*") == "tvsala"
    assert sanitize_inline_value(None) == ""
