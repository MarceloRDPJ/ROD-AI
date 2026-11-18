import pytest

from jarvis.core.router import _is_command, _is_llm_status, _is_question, route


def test_router_question_heuristics():
    assert _is_question("o que é usb")
    assert _is_question("como configurar docker")
    assert _is_question("quando é o dia das mães")
    assert _is_question("qual o meu ip?")


def test_router_command_heuristics():
    assert _is_command("status do servidor")
    assert _is_command("bloquear site youtube.com")
    assert _is_command("criar lembrete")


def test_router_llm_status_heuristic():
    assert _is_llm_status("llm")
    assert _is_llm_status("status da llm")


@pytest.mark.asyncio
async def test_router_llm_status_does_not_generate():
    result = await route("Llm")

    assert result["intent"] == "chat"
    assert result["source"] == "local_mode_status"
    assert "desativada" in result["params"]["response"]


@pytest.mark.asyncio
async def test_router_routes_natural_reminder_without_llm():
    result = await route("Me lembra teste 2 min")

    assert result["intent"] == "reminder_set"
    assert result["params"]["text"] == "teste"
    assert result["params"]["minutes"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "speed",
        "SPEED",
        "speed teste",
        "sped",
        "sped treste",
        "qual a velocidade da net",
        "teste velocdade internet",
    ],
)
async def test_router_tolerates_speedtest_typos(text):
    result = await route(text)

    assert result["intent"] == "network_speed"


@pytest.mark.asyncio
async def test_router_keeps_state_changing_actions_strict():
    result = await route("bluqear sit")

    assert result["intent"] != "network_block_site"


@pytest.mark.asyncio
async def test_speed_typo_never_becomes_a_state_changing_action():
    result = await route("sped treste")

    assert result["intent"] == "network_speed"
    assert result["intent"] not in {"network_block_site", "network_block_device"}
