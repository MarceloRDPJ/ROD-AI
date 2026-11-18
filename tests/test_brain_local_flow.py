from unittest.mock import AsyncMock, MagicMock

import pytest

from jarvis.core.brain import Brain


@pytest.mark.asyncio
async def test_brain_uses_local_clarification_for_open_question():
    brain = Brain()
    brain.local_brain.process = AsyncMock(return_value=None)
    result = await brain.process_intent("explique um assunto aleatorio")
    assert result["source"] == "local_clarification"
    assert result["params"]["response"]


@pytest.mark.asyncio
async def test_brain_does_not_invent_realtime_data_without_external_access():
    brain = Brain()
    brain.local_brain.process = AsyncMock(return_value=None)
    result = await brain.process_intent("tabela do brasileirao")
    assert result["source"] == "brasileirao_config"
    assert "fonte local gratuita" in result["params"]["response"].lower()


@pytest.mark.asyncio
async def test_brain_answers_mothers_day_with_local_calendar_rule():
    brain = Brain()
    brain.local_brain.process = AsyncMock(return_value=None)
    result = await brain.process_intent("quando e o dia das maes")
    assert result["source"] == "local_calendar"
    assert "segundo domingo de maio" in result["params"]["response"]


@pytest.mark.asyncio
async def test_brain_returns_collected_current_context_directly():
    brain = Brain()
    brain.local_brain.process = AsyncMock(return_value=None)
    brain.current_info.collect = MagicMock(
        return_value=MagicMock(
            ok=True,
            answer="",
            context="Cotação USD-BRL: Compra 5.10",
            source="awesomeapi",
            error="",
        )
    )
    result = await brain.process_intent("cotacao do dolar agora")
    assert result["source"] == "awesomeapi"
    assert result["params"]["response"] == "Cotação USD-BRL: Compra 5.10"
