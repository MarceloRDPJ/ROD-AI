from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jarvis.config import Config
from jarvis.core.brain import Brain
from jarvis.core.context import ContextEngine
from jarvis.core.executor import Executor
from jarvis.modules.hydration import HydrationModule


@pytest.mark.asyncio
async def test_executor_routes_hydration_setup():
    executor = Executor(MagicMock())
    chat_id = 12345
    Config.ALLOWED_USER_ID = chat_id
    context = {"flow": {"type": "hydration_setup", "step": "ask_goal", "data": {}}}
    with patch.object(ContextEngine, "get_context", return_value=context), patch.object(
        HydrationModule, "handle_flow", return_value="Setup Handled"
    ) as handle:
        response = await executor.execute(
            {"intent": "flow_input", "params": {"text": "5000"}}, chat_id
        )
    handle.assert_called_once()
    assert response == "Setup Handled"


@pytest.mark.asyncio
async def test_brain_local_clarification_fallback():
    brain = Brain()
    brain.local_brain.process = AsyncMock(return_value=None)
    result = await brain.process_intent("some random text")
    assert result["intent"] == "chat"
    assert result["params"]["response"]
    assert result["source"] == "local_clarification"
