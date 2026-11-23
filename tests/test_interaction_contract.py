import pytest

from jarvis.core.router import route


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("callback", "expected_intent"),
    [
        ("help", "help"),
        ("menu_rede", "menu_rede"),
        ("menu_agenda", "menu_agenda"),
        ("menu_automacoes", "menu_automacoes"),
        ("menu_sistema", "menu_sistema"),
        ("quem é você", "identity_who"),
        ("quem ta na rede", "network_scan"),
        ("velocidade da internet", "network_speed"),
        ("estatisticas de rede", "network_stats"),
        ("status da internet", "network_status"),
        ("listar lembretes", "reminder_list"),
        ("criar lembrete", "reminder_set"),
        ("ativar hidratacao", "hydration_activate"),
        ("analise de hidratacao", "hydration_analytics"),
        ("status hidratacao", "hydration_status"),
        ("bebi agua", "hydration_log_explicit"),
        ("agora nao", "chat"),
        ("listar automacoes", "automation_list"),
        ("config automacoes", "automation_config"),
        ("status do sistema", "system_status"),
        ("logs do sistema", "system_logs"),
        ("ajuda bloquear", "chat"),
        ("ajuda renomear", "chat"),
        ("confirmar", "action_confirm"),
        ("cancelar", "action_cancel"),
    ],
)
async def test_menu_callback_routes_to_a_real_intent(callback, expected_intent):
    result = await route(callback, chat_id=123)

    assert result["intent"] == expected_intent


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["bluqear sit", "bloqueia qualquer coisa", "bloquear ele ai"])
async def test_ambiguous_dangerous_language_never_routes_to_block(text):
    result = await route(text, chat_id=123)

    assert result["intent"] not in {"network_block_device", "network_block_site"}
